"""
Company Files Service (app/modules/company_files/service.py)
Core business logic for admin-defined company CSV files.
Pure Python domain layer - strictly no motor, pymongo, fastapi, or csv imports.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from app.core.config import settings
from app.core.exceptions import (
    AppError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.modules.company_files.interfaces import (
    AuditServiceProtocol,
    CacheProtocol,
    CompanyFilesCsvParserProtocol,
    CompanyFilesRepoProtocol,
    TenantSchemaProtocol,
)
from app.modules.company_files.models import (
    create_file_document,
    create_row_document,
    slugify_name,
)


class CompanyFilesService:
    def __init__(
        self,
        repo: CompanyFilesRepoProtocol,
        parser: CompanyFilesCsvParserProtocol,
        tenant_repo: TenantSchemaProtocol,
        cache: CacheProtocol,
        audit: Optional[AuditServiceProtocol] = None,
        file_connections_service: Optional[Any] = None,
        enrichment_worker: Optional[Any] = None,
    ):
        self.repo = repo
        self.parser = parser
        self.tenant_repo = tenant_repo
        self.cache = cache
        self.audit = audit
        self.file_connections_service = file_connections_service
        self.enrichment_worker = enrichment_worker

    async def _check_rate_limit(self, company_id: str) -> None:
        key = f"rate:company_files_import:{company_id}"
        count = await self.cache.incr(key)
        if count == 1:
            await self.cache.expire(key, 3600)
        if count > settings.IMPORT_MAX_PER_HOUR:
            raise RateLimitedError(
                f"Import rate limit of {settings.IMPORT_MAX_PER_HOUR} per hour exceeded.",
                retry_after=3600,
            )

    async def _audit_log(self, user_id: str, company_id: str, action: str, target: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if not self.audit:
            return
        try:
            if hasattr(self.audit, "log_event"):
                await self.audit.log_event(actor=str(user_id), action=action, target=target, result="success", metadata=metadata)
            elif hasattr(self.audit, "record_event"):
                await self.audit.record_event(
                    actor={"id": user_id, "role": "company_admin", "company_id": company_id},
                    action=action,
                    target=target,
                    result="success",
                )
        except Exception:
            pass

    async def preview(self, company_id: str, content_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Preview CSV: type inference, column sanitization, sample rows, errors. Writes nothing."""
        res = self.parser.parse_preview(
            content_bytes=content_bytes,
            filename=filename,
            max_files=settings.FILES_MAX_PER_COMPANY,
            max_mb=settings.IMPORT_MAX_FILE_MB,
            max_rows=settings.IMPORT_MAX_ROWS,
            max_columns=settings.IMPORT_MAX_COLUMNS,
            max_cell_chars=settings.IMPORT_MAX_CELL_CHARS,
            max_preview_errors=settings.IMPORT_PREVIEW_ERRORS,
        )
        raw_rows = res.pop("_raw_rows", [])
        detected_connections = []
        try:
            from app.infrastructure.file_connections.preview_detector import detect_connections_for_preview
            conn_repo = getattr(self.file_connections_service, "conn_repo", None)
            if conn_repo:
                detected_connections = await detect_connections_for_preview(
                    company_id=company_id,
                    preview_file_name=filename,
                    preview_columns=res.get("columns", []),
                    raw_rows=raw_rows,
                    files_repo=self.repo,
                    conn_repo=conn_repo,
                )
        except Exception:
            pass
        res["detected_connections"] = detected_connections
        return res

    async def create_file(
        self,
        company_id: str,
        user_id: str,
        content_bytes: bytes,
        filename: str,
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a new company file with parsed and typed rows."""
        # 1. Concurrency & Rate Limit
        await self._check_rate_limit(company_id)
        active = await self.repo.get_active_import(company_id)
        if active:
            raise AppError(
                code="IMPORT_IN_PROGRESS",
                message="Another import for this company is already in progress.",
                http_status=409,
            )

        # 2. Limit check
        count = await self.repo.count_files(company_id)
        if count >= settings.FILES_MAX_PER_COMPANY:
            raise AppError(
                code="FILE_LIMIT_REACHED",
                message=f"Maximum allowed limit of {settings.FILES_MAX_PER_COMPANY} files reached.",
                http_status=409,
            )

        # 3. Validation: name and description
        name = (meta.get("name") or "").strip()
        if len(name) < 2 or len(name) > 60:
            raise ValidationError("File name must be between 2 and 60 characters.")

        description = (meta.get("description") or "").strip()
        if len(description) < 5 or len(description) > 500:
            raise ValidationError("Description must be between 5 and 500 characters.")

        upload_mode = meta.get("upload_mode", "replace")
        if upload_mode not in ("replace", "append"):
            upload_mode = "replace"

        # Check name uniqueness (case-insensitive)
        existing_name = await self.repo.get_file_by_name(company_id, name)
        if existing_name:
            raise AppError(
                code="FILE_EXISTS",
                message="A file with this name already exists for your company.",
                http_status=409,
            )

        # 4. Generate unique slug
        base_slug = slugify_name(name)
        slug = base_slug
        slug_count = 1
        while await self.repo.get_file_by_slug(company_id, slug):
            slug_count += 1
            slug = f"{base_slug}_{slug_count}"[:40]

        # 5. Parse file preview to detect columns
        preview_data = self.parser.parse_preview(
            content_bytes=content_bytes,
            filename=filename,
            max_files=settings.FILES_MAX_PER_COMPANY,
            max_mb=settings.IMPORT_MAX_FILE_MB,
            max_rows=settings.IMPORT_MAX_ROWS,
            max_columns=settings.IMPORT_MAX_COLUMNS,
            max_cell_chars=settings.IMPORT_MAX_CELL_CHARS,
            max_preview_errors=settings.IMPORT_PREVIEW_ERRORS,
        )

        detected_columns = preview_data["columns"]
        detected_keys = {c["key"] for c in detected_columns}

        # Merge user-supplied column meta (only meaning and hidden for keys that exist)
        meta_columns = {c["key"]: c for c in meta.get("columns", []) if isinstance(c, dict) and "key" in c}
        final_columns: List[Dict[str, Any]] = []
        for col in detected_columns:
            k = col["key"]
            user_col = meta_columns.get(k, {})
            meaning = user_col.get("meaning")
            if meaning and len(str(meaning)) > 120:
                meaning = str(meaning)[:120]
            hidden = bool(user_col.get("hidden", False))

            final_columns.append({
                "key": k,
                "label": col["label"],
                "type": col["type"],
                "example": col.get("example"),
                "meaning": meaning if meaning else None,
                "hidden": hidden,
                "role": col.get("role"),
                "role_confidence": col.get("role_confidence"),
                "profile": col.get("profile"),
                "linked_to": col.get("linked_to", []),
            })

        import_id = uuid.uuid4().hex

        # 6. Insert metadata record (status "processing")
        file_doc = create_file_document(
            company_id=company_id,
            name=name,
            slug=slug,
            description=description,
            original_filename=filename,
            upload_mode=upload_mode,
            columns=final_columns,
            import_id=import_id,
            data_health=preview_data.get("data_health", {}),
        )
        file_id = await self.repo.create_file_record(file_doc)

        # 7. Ingestion: parse rows and insert in batches of 1,000
        try:
            valid_rows, skipped_count, errors, _ = self.parser.parse_for_ingestion(
                content_bytes=content_bytes,
                columns=final_columns,
                max_mb=settings.IMPORT_MAX_FILE_MB,
                max_rows=settings.IMPORT_MAX_ROWS,
                max_columns=settings.IMPORT_MAX_COLUMNS,
                max_cell_chars=settings.IMPORT_MAX_CELL_CHARS,
            )

            # Insert in batches of 1,000
            batch: List[Dict[str, Any]] = []
            for r in valid_rows:
                row_doc = create_row_document(
                    company_id=company_id,
                    file_id=file_id,
                    import_id=import_id,
                    data=r,
                )
                batch.append(row_doc)
                if len(batch) >= 1000:
                    await self.repo.insert_rows_batch(batch)
                    batch = []
            if batch:
                await self.repo.insert_rows_batch(batch)

            # Compute stats
            stats = await self.repo.compute_file_stats(company_id, file_id, final_columns)

            # Update file record to "ready"
            await self.repo.update_file_record(file_id, {
                "status": "ready",
                "row_count": len(valid_rows),
                "stats": stats,
            })

        except Exception:
            # Atomic rollback on any failure: delete rows and delete metadata record
            try:
                await self.repo.delete_rows_by_import(company_id, file_id, import_id)
                await self.repo.delete_file_record(company_id, file_id)
            except Exception:
                pass
            raise

        # 8. Post-maintenance: bump schema_version, clear query cache, record audit
        await self.tenant_repo.bump_schema_version(company_id)
        await self.cache.clear_tenant(company_id)
        try:
            from app.core.cache import invalidate_company_cache
            await invalidate_company_cache(company_id)
        except Exception:
            pass
        try:
            from app.core.cache import invalidate_company_cache
            await invalidate_company_cache(company_id)
        except Exception:
            pass

        await self._audit_log(
            user_id=user_id,
            company_id=company_id,
            action="company_file.created",
            target=str(file_id),
            metadata={"inserted": len(valid_rows), "skipped": skipped_count},
        )

        if self.file_connections_service:
            try:
                import asyncio
                asyncio.create_task(self.file_connections_service.generate_suggestions(company_id))
            except Exception:
                pass

        return {
            "id": str(file_id),
            "status": "ready",
            "inserted": len(valid_rows),
            "skipped": skipped_count,
        }

    async def list_files(self, company_id: str) -> Dict[str, Any]:
        """List company files and compute summary."""
        files = await self.repo.list_files(company_id)
        total_rows = sum(f.get("row_count", 0) for f in files)
        last_upload = files[0].get("updated_at") if files else None

        items: List[Dict[str, Any]] = []
        for f in files:
            items.append({
                "id": str(f["_id"]),
                "name": f.get("name", ""),
                "description": f.get("description", ""),
                "rows": f.get("row_count", 0),
                "columns_count": len(f.get("columns", [])),
                "hidden_columns_count": sum(1 for c in f.get("columns", []) if c.get("hidden")),
                "updated_at": f.get("updated_at", datetime.now(timezone.utc)),
                "status": f.get("status", "ready"),
                "upload_mode": f.get("upload_mode", "replace"),
                "columns": f.get("columns", []),
            })

        return {
            "summary": {
                "files": len(files),
                "total_rows": total_rows,
                "last_upload": last_upload,
                "limit": settings.FILES_MAX_PER_COMPANY,
            },
            "files": items,
        }

    async def get_file_detail(self, company_id: str, file_id: str) -> Dict[str, Any]:
        """Get file detail with columns."""
        f = await self.repo.get_file_by_id(company_id, file_id)
        if not f:
            raise NotFoundError("File not found or does not belong to your company.")

        return {
            "id": str(f["_id"]),
            "name": f.get("name", ""),
            "slug": f.get("slug", ""),
            "description": f.get("description", ""),
            "status": f.get("status", "ready"),
            "upload_mode": f.get("upload_mode", "replace"),
            "row_count": f.get("row_count", 0),
            "columns": f.get("columns", []),
            "created_at": f.get("created_at", datetime.now(timezone.utc)),
            "updated_at": f.get("updated_at", datetime.now(timezone.utc)),
        }

    async def patch_file(
        self,
        company_id: str,
        user_id: str,
        file_id: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update file metadata, column meanings, or hidden flags. Changes no rows."""
        f = await self.repo.get_file_by_id(company_id, file_id)
        if not f:
            raise NotFoundError("File not found or does not belong to your company.")

        doc_updates: Dict[str, Any] = {}

        if "name" in updates and updates["name"]:
            new_name = updates["name"].strip()
            if len(new_name) < 2 or len(new_name) > 60:
                raise ValidationError("File name must be between 2 and 60 characters.")
            if new_name != f.get("name"):
                existing = await self.repo.get_file_by_name(company_id, new_name)
                if existing and str(existing["_id"]) != str(file_id):
                    raise AppError(code="FILE_EXISTS", message="A file with this name already exists.", http_status=409)
                doc_updates["name"] = new_name

        if "description" in updates and updates["description"]:
            new_desc = updates["description"].strip()
            if len(new_desc) < 5 or len(new_desc) > 500:
                raise ValidationError("Description must be between 5 and 500 characters.")
            doc_updates["description"] = new_desc

        if "upload_mode" in updates and updates["upload_mode"] in ("replace", "append"):
            doc_updates["upload_mode"] = updates["upload_mode"]

        if "columns" in updates and isinstance(updates["columns"], list):
            user_cols_map = {c["key"]: c for c in updates["columns"] if isinstance(c, dict) and "key" in c}
            current_columns = f.get("columns", [])
            updated_columns: List[Dict[str, Any]] = []
            for col in current_columns:
                k = col["key"]
                if k in user_cols_map:
                    uc = user_cols_map[k]
                    meaning = uc.get("meaning", col.get("meaning"))
                    if meaning and len(str(meaning)) > 120:
                        meaning = str(meaning)[:120]
                    hidden = bool(uc.get("hidden", col.get("hidden", False)))
                    updated_columns.append({**col, "meaning": meaning, "hidden": hidden})
                else:
                    updated_columns.append(col)
            doc_updates["columns"] = updated_columns

            # Recompute stats with updated hidden flags
            stats = await self.repo.compute_file_stats(company_id, file_id, updated_columns)
            doc_updates["stats"] = stats

        if doc_updates:
            await self.repo.update_file_record(file_id, doc_updates)
            await self.tenant_repo.bump_schema_version(company_id)
            await self.cache.clear_tenant(company_id)
            try:
                from app.core.cache import invalidate_company_cache
                await invalidate_company_cache(company_id)
            except Exception:
                pass

            await self._audit_log(
                user_id=user_id,
                company_id=company_id,
                action="company_file.updated",
                target=str(file_id),
                metadata={"fields_updated": list(doc_updates.keys())},
            )

            if "columns" in doc_updates and self.enrichment_worker:
                try:
                    import asyncio
                    asyncio.create_task(self.enrichment_worker.on_file_updated(company_id, file_id))
                except Exception:
                    pass

        return await self.get_file_detail(company_id, file_id)

    async def upload_file(
        self,
        company_id: str,
        user_id: str,
        file_id: str,
        content_bytes: bytes,
        filename: str,
    ) -> Dict[str, Any]:
        """Upload a new CSV for an existing file (replace or append mode)."""
        f = await self.repo.get_file_by_id(company_id, file_id)
        if not f:
            raise NotFoundError("File not found or does not belong to your company.")

        await self._check_rate_limit(company_id)
        active = await self.repo.get_active_import(company_id)
        if active and str(active["_id"]) != str(file_id):
            raise AppError(
                code="IMPORT_IN_PROGRESS",
                message="Another import for this company is already in progress.",
                http_status=409,
            )

        saved_columns = f.get("columns", [])
        upload_mode = f.get("upload_mode", "replace")
        new_import_id = uuid.uuid4().hex

        # Mark status "processing"
        await self.repo.update_file_record(file_id, {"status": "processing"})

        try:
            valid_rows, skipped_count, errors, _ = self.parser.parse_for_ingestion(
                content_bytes=content_bytes,
                columns=saved_columns,
                max_mb=settings.IMPORT_MAX_FILE_MB,
                max_rows=settings.IMPORT_MAX_ROWS,
                max_columns=settings.IMPORT_MAX_COLUMNS,
                max_cell_chars=settings.IMPORT_MAX_CELL_CHARS,
            )

            # Insert valid rows in batches of 1,000
            batch: List[Dict[str, Any]] = []
            for r in valid_rows:
                row_doc = create_row_document(
                    company_id=company_id,
                    file_id=file_id,
                    import_id=new_import_id,
                    data=r,
                )
                batch.append(row_doc)
                if len(batch) >= 1000:
                    await self.repo.insert_rows_batch(batch)
                    batch = []
            if batch:
                await self.repo.insert_rows_batch(batch)

            if upload_mode == "replace":
                # Delete older rows after success
                await self.repo.delete_old_rows(company_id, file_id, keep_import_id=new_import_id)

            # Count total rows
            total_rows = await self.repo.count_file_rows(company_id, file_id)
            stats = await self.repo.compute_file_stats(company_id, file_id, saved_columns)

            await self.repo.update_file_record(file_id, {
                "status": "ready",
                "row_count": total_rows,
                "stats": stats,
                "last_import_id": new_import_id,
                "original_filename": filename,
            })

        except Exception:
            # On failure: delete rows with new_import_id and revert status to ready
            try:
                await self.repo.delete_rows_by_import(company_id, file_id, new_import_id)
                await self.repo.update_file_record(file_id, {"status": "ready"})
            except Exception:
                pass
            raise

        await self.tenant_repo.bump_schema_version(company_id)
        await self.cache.clear_tenant(company_id)
        try:
            from app.core.cache import invalidate_company_cache
            await invalidate_company_cache(company_id)
        except Exception:
            pass

        await self._audit_log(
            user_id=user_id,
            company_id=company_id,
            action="company_file.uploaded",
            target=str(file_id),
            metadata={"inserted": len(valid_rows), "skipped": skipped_count, "mode": upload_mode},
        )

        if self.enrichment_worker:
            try:
                import asyncio
                asyncio.create_task(self.enrichment_worker.on_file_updated(company_id, file_id))
            except Exception:
                pass
        if self.file_connections_service:
            try:
                import asyncio
                asyncio.create_task(self.file_connections_service.generate_suggestions(company_id))
            except Exception:
                pass

        return {
            "inserted": len(valid_rows),
            "skipped": skipped_count,
            "errors": errors[:20],
            "status": "ready",
        }

    async def delete_file(self, company_id: str, user_id: str, file_id: str) -> None:
        """Delete file metadata and all associated company rows."""
        f = await self.repo.get_file_by_id(company_id, file_id)
        if not f:
            raise NotFoundError("File not found or does not belong to your company.")

        await self.repo.delete_all_file_rows(company_id, file_id)
        await self.repo.delete_file_record(company_id, file_id)

        if self.enrichment_worker:
            try:
                import asyncio
                asyncio.create_task(self.enrichment_worker.on_file_deleted(company_id, file_id))
            except Exception:
                pass

        await self.tenant_repo.bump_schema_version(company_id)
        await self.cache.clear_tenant(company_id)
        try:
            from app.core.cache import invalidate_company_cache
            await invalidate_company_cache(company_id)
        except Exception:
            pass

        await self._audit_log(
            user_id=user_id,
            company_id=company_id,
            action="company_file.deleted",
            target=str(file_id),
        )
