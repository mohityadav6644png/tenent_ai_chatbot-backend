"""
Company Service (app/modules/company/service.py)
Core business logic for company records and structured dataset imports.
Strictly decoupled from motor, pymongo, pandas, and FastAPI UploadFile.
"""

from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from app.core.config import settings
from app.modules.company.datasets import DATASET_CATALOG, DatasetDefinition, get_dataset_definition
from app.modules.company.interfaces import (
    CacheClientProtocol,
    CompanyRecordsRepoProtocol,
    CsvParserProtocol,
    DataImportTrackingRepoProtocol,
    DataIngestionRepoProtocol,
    TenantSchemaProtocol,
)
from app.modules.company.models import (
    create_company_record_document,
    create_data_import_document,
)

# Known aliases for column mapping
KNOWN_TITLE_ALIASES = {
    "title",
    "document title",
    "document_title",
    "documenttitle",
    "doc title",
    "doc_title",
    "heading",
    "header",
    "subject",
    "topic",
    "name",
    "record name",
    "item name",
    "product name",
    "item",
    "product",
    "question",
    "prompt",
    "query",
    "faq",
    "article",
    "summary",
    "headline",
    "policy",
    "rule",
    "task",
    "key",
}

KNOWN_CONTENT_ALIASES = {
    "content",
    "body content",
    "body_content",
    "bodycontent",
    "body",
    "text",
    "description",
    "document content",
    "document_content",
    "record content",
    "record_content",
    "body text",
    "body_text",
    "details",
    "detail",
    "notes",
    "note",
    "answer",
    "response",
    "completion",
    "solution",
    "explanation",
    "message",
    "information",
    "info",
    "data",
    "value",
    "overview",
    "definition",
}

CONTROL_CHAR_REGEX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _sanitize_header_name(name: str) -> str:
    """Sanitize header name to max 60 safe characters."""
    stripped = str(name).strip()
    safe = re.sub(r"[^A-Za-z0-9_ \-]", "", stripped)
    return safe[:60]


def _normalize_header(name: str) -> str:
    """Normalize header name by lowercasing, replacing punctuation/underscores with spaces."""
    cleaned = str(name).strip().lower().replace("_", " ").replace("-", " ")
    return " ".join(cleaned.split())


def _has_forbidden_control_chars(text: str) -> bool:
    """Reject control characters except \n, \r, and \t."""
    return bool(CONTROL_CHAR_REGEX.search(text))


class CompanyDataError(Exception):
    """Domain exception for company data operations."""

    def __init__(
        self,
        message: str,
        code: str = "BAD_REQUEST",
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
        retry_after: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details
        self.retry_after = retry_after


class CompanyService:
    """Service layer managing free-text company records and structured datasets."""

    def __init__(
        self,
        records_repo: Optional[CompanyRecordsRepoProtocol] = None,
        ingestion_repo: Optional[DataIngestionRepoProtocol] = None,
        tracking_repo: Optional[DataImportTrackingRepoProtocol] = None,
        csv_parser: Optional[CsvParserProtocol] = None,
        tenant_repo: Optional[TenantSchemaProtocol] = None,
        cache: Optional[CacheClientProtocol] = None,
        audit_service: Any = None,
    ):
        self.records_repo = records_repo
        self.ingestion_repo = ingestion_repo
        self.tracking_repo = tracking_repo
        self.csv_parser = csv_parser
        self.tenant_repo = tenant_repo
        self.cache = cache
        self.audit_service = audit_service

    # -------------------------------------------------------------------------
    # Feature A: Free-text records
    # -------------------------------------------------------------------------

    async def insert_manual_record(
        self,
        company_id: str,
        title: str,
        content: str,
        user_id: Optional[str] = None,
        db: Any = None,
    ) -> str:
        """Insert a single text record into company_records stamped with company_id."""
        clean_title = (title or "").strip()
        clean_content = (content or "").strip()

        if not clean_title or not clean_content:
            raise CompanyDataError(
                "Title and content cannot be empty.",
                code="VALIDATION_ERROR",
                status_code=400,
            )

        title_max = getattr(settings, "RECORD_TITLE_MAX", 200)
        content_max = getattr(settings, "RECORD_CONTENT_MAX", 20000)

        if len(clean_title) > title_max:
            raise CompanyDataError(
                f"Title exceeds maximum length of {title_max} characters.",
                code="VALIDATION_ERROR",
                status_code=400,
            )

        if len(clean_content) > content_max:
            raise CompanyDataError(
                f"Content exceeds maximum length of {content_max} characters.",
                code="VALIDATION_ERROR",
                status_code=400,
            )

        if _has_forbidden_control_chars(clean_title) or _has_forbidden_control_chars(clean_content):
            raise CompanyDataError(
                "Title and content cannot contain control characters.",
                code="VALIDATION_ERROR",
                status_code=400,
            )

        doc = create_company_record_document(
            company_id=company_id,
            title=clean_title,
            content=clean_content,
            created_at=datetime.now(UTC),
            created_by=user_id,
        )

        # Use db override if provided (e.g. mock_db in integration tests)
        if db is not None:
            collection = db["company_records"]
            result = await collection.insert_one(doc)
            return str(result.inserted_id)

        if self.records_repo is not None:
            return await self.records_repo.insert_record(doc)

        from app.infrastructure.db.company_repo import MongoCompanyRecordsRepository

        repo = MongoCompanyRecordsRepository()
        return await repo.insert_record(doc)

    async def import_csv_data(
        self,
        company_id: str,
        content_bytes: bytes,
        filename: str = "upload.csv",
        user_id: Optional[str] = None,
        title_column: Optional[str] = None,
        content_column: Optional[str] = None,
        custom_mapping_raw: Optional[str] = None,
        db: Any = None,
    ) -> Tuple[int, int, str]:
        """
        Parses an uploaded CSV file, resolves title and content columns,
        stamps rows with verified company_id, user_id, UTC created_at,
        and executes bulk insert into company_records.
        Returns: (inserted_count, skipped_count, import_id)
        """
        # 1. Rate limiting check
        max_per_hour = getattr(settings, "IMPORT_MAX_PER_HOUR", 10)
        if db is not None:
            tracking_coll = db["data_imports"] if hasattr(db, "__getitem__") else None
            if tracking_coll is not None and hasattr(tracking_coll, "count_documents"):
                try:
                    recent = await tracking_coll.count_documents({"company_id": str(company_id)})
                    if recent >= max_per_hour:
                        raise CompanyDataError(
                            "Import rate limit exceeded. Please wait before importing again.",
                            code="RATE_LIMITED",
                            status_code=429,
                            retry_after=3600,
                        )
                except CompanyDataError:
                    raise
                except Exception:
                    pass
        elif self.tracking_repo is not None:
            try:
                recent_count = await self.tracking_repo.count_recent_imports(company_id, hours=1)
                if recent_count >= max_per_hour:
                    raise CompanyDataError(
                        "Import rate limit exceeded. Please wait before importing again.",
                        code="RATE_LIMITED",
                        status_code=429,
                        retry_after=3600,
                    )
            except CompanyDataError:
                raise
            except Exception:
                pass

        # 2. Validate file extension
        clean_filename = (filename or "").strip()
        if not clean_filename.lower().endswith((".csv", ".txt")):
            raise CompanyDataError(
                "Invalid file format. Please upload a valid CSV file.",
                code="INVALID_FILE_FORMAT",
                status_code=400,
            )

        # 3. Parse with CSV parser
        if self.csv_parser is None:
            from app.infrastructure.company.csv_parser import StandardCsvParser

            self.csv_parser = StandardCsvParser()

        try:
            headers, raw_rows, skipped_count = self.csv_parser.parse_csv_stream(
                content_bytes,
                max_file_mb=getattr(settings, "IMPORT_MAX_FILE_MB", 5),
                max_rows=getattr(settings, "IMPORT_MAX_ROWS", 50000),
                max_columns=getattr(settings, "IMPORT_MAX_COLUMNS", 30),
                max_cell_chars=getattr(settings, "IMPORT_MAX_CELL_CHARS", 2000),
            )
        except Exception as exc:
            code = getattr(exc, "code", "BAD_REQUEST")
            status_code = getattr(exc, "status_code", 400)
            raise CompanyDataError(str(exc), code=code, status_code=status_code)

        # 4. Resolve title and content column mapping
        resolved_title_col: Optional[str] = None
        resolved_content_col: Optional[str] = None

        # Check explicit column parameters
        if title_column and title_column.strip():
            for h in headers:
                if h.strip() == title_column.strip() or _normalize_header(h) == _normalize_header(title_column):
                    resolved_title_col = h
                    break
        if content_column and content_column.strip():
            for h in headers:
                if h.strip() == content_column.strip() or _normalize_header(h) == _normalize_header(content_column):
                    resolved_content_col = h
                    break

        # Check custom_mapping_raw if not resolved
        custom_mapping: Dict[str, str] = {}
        if custom_mapping_raw:
            try:
                custom_mapping = json.loads(custom_mapping_raw)
            except Exception:
                raise CompanyDataError(
                    "Invalid custom column mapping. Must be valid JSON format.",
                    code="INVALID_MAPPING",
                    status_code=400,
                )
            for raw_col, target in custom_mapping.items():
                target_clean = str(target).strip().lower()
                for h in headers:
                    if h == raw_col or _normalize_header(h) == _normalize_header(raw_col):
                        if target_clean == "title" and not resolved_title_col:
                            resolved_title_col = h
                        elif target_clean == "content" and not resolved_content_col:
                            resolved_content_col = h

        # Apply heuristic matching for unmapped columns
        for h in headers:
            norm = _normalize_header(h)
            compact = norm.replace(" ", "")
            if not resolved_title_col and (norm in KNOWN_TITLE_ALIASES or compact in KNOWN_TITLE_ALIASES):
                resolved_title_col = h
            elif not resolved_content_col and (norm in KNOWN_CONTENT_ALIASES or compact in KNOWN_CONTENT_ALIASES):
                resolved_content_col = h

        # Fallback 1: word-level matching if still missing title or content
        if not resolved_title_col or not resolved_content_col:
            title_keywords = {
                "title", "heading", "header", "subject", "topic", "question",
                "name", "article", "prompt", "faq", "policy", "summary", "key"
            }
            content_keywords = {
                "content", "body", "text", "description", "details", "notes",
                "answer", "response", "solution", "info", "message", "explanation"
            }
            for h in headers:
                norm_words = set(_normalize_header(h).split())
                if not resolved_title_col and (norm_words & title_keywords):
                    if h != resolved_content_col:
                        resolved_title_col = h
                elif not resolved_content_col and (norm_words & content_keywords):
                    if h != resolved_title_col:
                        resolved_content_col = h

        # Fallback 2: If file has exactly 2 columns and neither matched keywords
        if len(headers) == 2:
            if not resolved_title_col and not resolved_content_col:
                resolved_title_col = headers[0]
                resolved_content_col = headers[1]
            elif resolved_title_col and not resolved_content_col:
                other = [h for h in headers if h != resolved_title_col]
                if other:
                    resolved_content_col = other[0]
            elif not resolved_title_col and resolved_content_col:
                other = [h for h in headers if h != resolved_content_col]
                if other:
                    resolved_title_col = other[0]

        # If still missing title or content: HTTP 400 MISSING_COLUMNS with details
        if not resolved_title_col or not resolved_content_col:
            missing_cols = [c for c in ("title", "content") if not (resolved_title_col if c == "title" else resolved_content_col)]
            sanitized_headers = [_sanitize_header_name(h) for h in headers[:20]]
            raise CompanyDataError(
                f"Missing required columns: {', '.join(missing_cols)}. "
                f"Your file needs a title column and a content column. "
                f"Available headers: {sanitized_headers}.",
                code="MISSING_COLUMNS",
                status_code=400,
                details={"found_columns": sanitized_headers},
            )

        # 5. Extract and validate rows
        import_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        title_max = getattr(settings, "RECORD_TITLE_MAX", 200)
        content_max = getattr(settings, "RECORD_CONTENT_MAX", 20000)

        records: List[Dict[str, Any]] = []
        for row in raw_rows:
            raw_title = row.get(resolved_title_col, "")
            raw_content = row.get(resolved_content_col, "")

            clean_title = raw_title.strip()
            clean_content = raw_content.strip()

            # Skip completely empty rows
            if not clean_title and not clean_content:
                skipped_count += 1
                continue

            if len(clean_title) > title_max:
                clean_title = clean_title[:title_max]
            if len(clean_content) > content_max:
                clean_content = clean_content[:content_max]

            records.append(
                create_company_record_document(
                    company_id=company_id,
                    title=clean_title,
                    content=clean_content,
                    created_at=now,
                    created_by=user_id,
                    import_id=import_id,
                )
            )

        if not records:
            raise CompanyDataError(
                "CSV file contains no valid data rows to import.",
                code="NO_DATA_ROWS",
                status_code=400,
            )

        # 6. Bulk write to company_records
        inserted_count = 0
        if db is not None:
            coll = db["company_records"]
            try:
                res = await coll.insert_many(records, ordered=False)
            except TypeError:
                res = await coll.insert_many(records)
            inserted_count = len(res.inserted_ids)
        elif self.records_repo is not None:
            inserted_count = await self.records_repo.bulk_insert_records(records)
        else:
            from app.infrastructure.db.company_repo import MongoCompanyRecordsRepository

            repo = MongoCompanyRecordsRepository()
            inserted_count = await repo.bulk_insert_records(records)

        # 7. Record tracking and audit
        if db is None and self.tracking_repo is not None:
            try:
                await self.tracking_repo.create_import(
                    {
                        "import_id": import_id,
                        "company_id": str(company_id),
                        "dataset": "records",
                        "mode": "insert",
                        "created_by": user_id,
                        "filename": clean_filename,
                        "status": "completed",
                        "started_at": now,
                        "completed_at": datetime.now(UTC),
                        "inserted": inserted_count,
                        "updated": 0,
                        "skipped": skipped_count,
                    }
                )
            except Exception:
                pass

        if self.audit_service is not None:
            try:
                await self.audit_service.log_event(
                    actor=user_id or "company_admin",
                    action="import_records",
                    target=company_id,
                    result="success",
                )
            except Exception:
                pass

        return inserted_count, skipped_count, import_id

    # -------------------------------------------------------------------------
    # Feature B: Structured Datasets
    # -------------------------------------------------------------------------

    async def list_datasets(self, company_id: str) -> List[Dict[str, Any]]:
        """List all structured datasets with current status, record counts, and last updated time."""
        result = []
        stale_days = getattr(settings, "DATASET_STALE_DAYS", 30)
        cutoff = datetime.now(UTC) - timedelta(days=stale_days)

        for key, definition in DATASET_CATALOG.items():
            records_count = 0
            last_updated: Optional[datetime] = None

            if self.ingestion_repo is not None:
                records_count, last_updated = await self.ingestion_repo.get_dataset_stats(
                    company_id=company_id,
                    collection_name=definition.collection_name,
                )

            # Determine status: "no_data", "needs_update", "ready"
            if records_count == 0:
                ds_status = "no_data"
            elif last_updated is not None and last_updated.tzinfo is None:
                # If naive datetime, make it UTC
                last_updated = last_updated.replace(tzinfo=UTC)
                if last_updated < cutoff:
                    ds_status = "needs_update"
                else:
                    ds_status = "ready"
            elif last_updated is not None and last_updated < cutoff:
                ds_status = "needs_update"
            else:
                ds_status = "ready"

            result.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                    "record_label": definition.record_label,
                    "records": records_count,
                    "last_updated": last_updated.isoformat() if last_updated else None,
                    "status": ds_status,
                    "required_columns": list(definition.required_columns),
                }
            )

        return result

    def get_dataset_template(self, dataset_key: str) -> str:
        """Generate a CSV template containing only the header row for the given dataset."""
        definition = get_dataset_definition(dataset_key)
        if not definition:
            raise CompanyDataError(
                f"Dataset '{dataset_key}' not found.",
                code="NOT_FOUND",
                status_code=404,
            )

        if self.csv_parser is None:
            from app.infrastructure.company.csv_parser import StandardCsvParser

            self.csv_parser = StandardCsvParser()

        return self.csv_parser.generate_template_csv(definition.all_columns)

    async def preview_dataset_import(
        self,
        company_id: str,
        dataset_key: str,
        content_bytes: bytes,
        filename: str = "upload.csv",
    ) -> Dict[str, Any]:
        """
        Validates CSV for the dataset without writing anything to the database.
        Returns validation summary, errors, and sample rows.
        """
        definition = get_dataset_definition(dataset_key)
        if not definition:
            raise CompanyDataError(
                f"Dataset '{dataset_key}' not found.",
                code="NOT_FOUND",
                status_code=404,
            )

        if self.csv_parser is None:
            from app.infrastructure.company.csv_parser import StandardCsvParser

            self.csv_parser = StandardCsvParser()

        headers, raw_rows, skipped = self.csv_parser.parse_csv_stream(
            content_bytes,
            max_file_mb=getattr(settings, "IMPORT_MAX_FILE_MB", 5),
            max_rows=getattr(settings, "IMPORT_MAX_ROWS", 50000),
            max_columns=getattr(settings, "IMPORT_MAX_COLUMNS", 30),
            max_cell_chars=getattr(settings, "IMPORT_MAX_CELL_CHARS", 2000),
        )

        header_set = {h.strip().lower(): h.strip() for h in headers}
        required_cols = list(definition.required_columns)
        all_cols_lower = {c.lower() for c in definition.all_columns}

        missing_columns = [c for c in required_cols if c.lower() not in header_set]
        unknown_columns = [h for h in headers if h.lower() not in all_cols_lower]

        errors: List[Dict[str, Any]] = []
        max_errors = getattr(settings, "IMPORT_PREVIEW_ERRORS", 500)
        valid_rows = 0
        invalid_rows = 0
        sample_rows: List[Dict[str, Any]] = []

        for row_idx, row in enumerate(raw_rows, start=2):
            row_has_error = False

            # Check required fields are non-empty
            for req_col in required_cols:
                matching_header = header_set.get(req_col.lower())
                val = row.get(matching_header, "").strip() if matching_header else ""
                if not val:
                    row_has_error = True
                    if len(errors) < max_errors:
                        errors.append(
                            {
                                "row": row_idx,
                                "column": req_col,
                                "reason": f"Required field '{req_col}' is missing or empty.",
                            }
                        )

            # Type validations
            for col_name, col_type in definition.field_types.items():
                matching_header = header_set.get(col_name.lower())
                if not matching_header:
                    continue
                val = row.get(matching_header, "").strip()
                if not val:
                    continue

                if col_type == "integer":
                    try:
                        int(val.replace(",", ""))
                    except ValueError:
                        row_has_error = True
                        if len(errors) < max_errors:
                            errors.append(
                                {
                                    "row": row_idx,
                                    "column": col_name,
                                    "reason": f"Value '{val}' is not a valid integer.",
                                }
                            )
                elif col_type == "float":
                    try:
                        float(val.replace(",", "").replace("$", "").replace("₹", ""))
                    except ValueError:
                        row_has_error = True
                        if len(errors) < max_errors:
                            errors.append(
                                {
                                    "row": row_idx,
                                    "column": col_name,
                                    "reason": f"Value '{val}' is not a valid number.",
                                }
                            )
                elif col_type == "email":
                    if not EMAIL_REGEX.match(val):
                        row_has_error = True
                        if len(errors) < max_errors:
                            errors.append(
                                {
                                    "row": row_idx,
                                    "column": col_name,
                                    "reason": f"Value '{val}' is not a valid email address.",
                                }
                            )

            if row_has_error:
                invalid_rows += 1
            else:
                valid_rows += 1

            if len(sample_rows) < 5:
                sample_rows.append(row)

        return {
            "total_rows": len(raw_rows),
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows,
            "required_columns": required_cols,
            "missing_columns": missing_columns,
            "unknown_columns": unknown_columns,
            "sample_rows": sample_rows,
            "errors": errors,
        }

    async def import_dataset(
        self,
        company_id: str,
        user_id: str,
        dataset_key: str,
        content_bytes: bytes,
        filename: str = "upload.csv",
        mode: str = "upsert",
    ) -> Dict[str, Any]:
        """
        Imports structured dataset via 'upsert' or 'replace'.
        Enforces concurrency guard, rate limiting, and cache invalidation.
        """
        definition = get_dataset_definition(dataset_key)
        if not definition:
            raise CompanyDataError(
                f"Dataset '{dataset_key}' not found.",
                code="NOT_FOUND",
                status_code=404,
            )

        clean_mode = str(mode).strip().lower()
        if clean_mode not in ("upsert", "replace"):
            raise CompanyDataError(
                f"Invalid import mode: '{mode}'. Must be 'upsert' or 'replace'.",
                code="INVALID_MODE",
                status_code=400,
            )

        # 1. Concurrency check
        if self.tracking_repo is not None:
            is_active = await self.tracking_repo.has_active_import(company_id, dataset_key, timeout_minutes=15)
            if is_active:
                raise CompanyDataError(
                    f"An import for dataset '{dataset_key}' is already in progress. Please wait for it to finish.",
                    code="IMPORT_IN_PROGRESS",
                    status_code=409,
                )

        # 2. Rate limiting check
        max_per_hour = getattr(settings, "IMPORT_MAX_PER_HOUR", 10)
        if self.tracking_repo is not None:
            recent_count = await self.tracking_repo.count_recent_imports(company_id, hours=1)
            if recent_count >= max_per_hour:
                raise CompanyDataError(
                    "Import rate limit exceeded. Please wait before importing again.",
                    code="RATE_LIMITED",
                    status_code=429,
                    retry_after=3600,
                )

        # 3. Parse CSV
        if self.csv_parser is None:
            from app.infrastructure.company.csv_parser import StandardCsvParser

            self.csv_parser = StandardCsvParser()

        headers, raw_rows, skipped_count = self.csv_parser.parse_csv_stream(
            content_bytes,
            max_file_mb=getattr(settings, "IMPORT_MAX_FILE_MB", 5),
            max_rows=getattr(settings, "IMPORT_MAX_ROWS", 50000),
            max_columns=getattr(settings, "IMPORT_MAX_COLUMNS", 30),
            max_cell_chars=getattr(settings, "IMPORT_MAX_CELL_CHARS", 2000),
        )

        header_set = {h.strip().lower(): h.strip() for h in headers}
        required_cols = list(definition.required_columns)
        missing_columns = [c for c in required_cols if c.lower() not in header_set]

        if missing_columns:
            sanitized = [_sanitize_header_name(h) for h in headers[:20]]
            raise CompanyDataError(
                f"Missing required columns for dataset '{dataset_key}': {missing_columns}.",
                code="MISSING_COLUMNS",
                status_code=400,
                details={"missing_columns": missing_columns, "found_columns": sanitized},
            )

        # 4. Cast and clean rows
        prepared_records: List[Dict[str, Any]] = []
        for row in raw_rows:
            cleaned_doc: Dict[str, Any] = {}
            row_valid = True

            for col_name in definition.all_columns:
                matching_header = header_set.get(col_name.lower())
                val = row.get(matching_header, "").strip() if matching_header else ""

                if col_name in required_cols and not val:
                    row_valid = False
                    break

                if not val:
                    cleaned_doc[col_name] = None
                    continue

                col_type = definition.field_types.get(col_name, "string")
                if col_type == "integer":
                    try:
                        cleaned_doc[col_name] = int(val.replace(",", ""))
                    except ValueError:
                        cleaned_doc[col_name] = None
                elif col_type == "float":
                    try:
                        cleaned_doc[col_name] = float(val.replace(",", "").replace("$", "").replace("₹", ""))
                    except ValueError:
                        cleaned_doc[col_name] = None
                elif col_type == "date":
                    try:
                        cleaned_doc[col_name] = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    except Exception:
                        cleaned_doc[col_name] = val
                else:
                    cleaned_doc[col_name] = val

            if not row_valid:
                skipped_count += 1
                continue

            prepared_records.append(cleaned_doc)

        if not prepared_records:
            raise CompanyDataError(
                "No valid data rows found to import.",
                code="NO_DATA_ROWS",
                status_code=400,
            )

        # 5. Track in-progress import
        import_id = uuid.uuid4().hex
        import_doc = create_data_import_document(
            import_id=import_id,
            company_id=company_id,
            dataset=dataset_key,
            mode=clean_mode,
            created_by=user_id,
            filename=filename,
        )
        if self.tracking_repo is not None:
            await self.tracking_repo.create_import(import_doc)

        # 6. Execute import
        inserted = 0
        updated = 0

        try:
            if self.ingestion_repo is None:
                from app.infrastructure.db.company_repo import MongoDataIngestionRepository

                self.ingestion_repo = MongoDataIngestionRepository()

            if clean_mode == "upsert":
                inserted, updated = await self.ingestion_repo.upsert_records(
                    company_id=company_id,
                    collection_name=definition.collection_name,
                    natural_key=definition.natural_key,
                    records=prepared_records,
                )
            else:  # replace
                inserted = await self.ingestion_repo.replace_records(
                    company_id=company_id,
                    collection_name=definition.collection_name,
                    import_id=import_id,
                    records=prepared_records,
                )
                updated = 0

            # 7. Post-import maintenance: bump schema_version and clear cache
            if self.tenant_repo is not None:
                try:
                    await self.tenant_repo.bump_schema_version(company_id)
                except Exception:
                    pass

            if self.tracking_repo is not None:
                await self.tracking_repo.update_import(
                    import_id,
                    {
                        "status": "completed",
                        "completed_at": datetime.now(UTC),
                        "inserted": inserted,
                        "updated": updated,
                        "skipped": skipped_count,
                    },
                )

            if self.audit_service is not None:
                try:
                    await self.audit_service.log_event(
                        actor=user_id,
                        action="import_dataset",
                        target=company_id,
                        result="success",
                    )
                except Exception:
                    pass

            return {
                "import_id": import_id,
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped_count,
                "status": "completed",
            }

        except Exception as exc:
            if self.tracking_repo is not None:
                await self.tracking_repo.update_import(
                    import_id,
                    {
                        "status": "failed",
                        "completed_at": datetime.now(UTC),
                        "error_message": str(exc),
                    },
                )
            raise
