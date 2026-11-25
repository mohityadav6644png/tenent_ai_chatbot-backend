"""
File Connections Service (app/modules/file_connections/service.py)
Core business logic for admin-defined file connections and suggestions.
Pure Python domain layer - strictly no motor, pymongo, fastapi, or csv imports.
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.modules.company_files.interfaces import CompanyFilesRepoProtocol
from app.modules.file_connections.interfaces import EnrichmentWorkerProtocol, FileConnectionsRepoProtocol
from app.modules.file_connections.models import create_connection_document, is_valid_slug
from app.modules.file_connections.validation import (
    calculate_match_metrics,
    check_type_compatibility,
    evaluate_right_key_uniqueness,
)


class FileConnectionsService:
    def __init__(
        self,
        conn_repo: FileConnectionsRepoProtocol,
        files_repo: CompanyFilesRepoProtocol,
        enrichment_worker: EnrichmentWorkerProtocol,
    ):
        self.conn_repo = conn_repo
        self.files_repo = files_repo
        self.worker = enrichment_worker

    async def _validate_connection_parties(
        self, company_id: str, left_file_id: str, left_key: str, right_file_id: str, right_key: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        if str(left_file_id) == str(right_file_id):
            raise ValidationError("Left and right files must be different.")

        left_file = await self.files_repo.get_file_by_id(company_id, left_file_id)
        right_file = await self.files_repo.get_file_by_id(company_id, right_file_id)
        if not left_file or not right_file or left_file.get("status") != "ready" or right_file.get("status") != "ready":
            raise NotFoundError("One or both company files not found or not in ready state.")

        left_cols = {c["key"]: c for c in left_file.get("columns", [])}
        right_cols = {c["key"]: c for c in right_file.get("columns", [])}
        if left_key not in left_cols or right_key not in right_cols:
            raise ValidationError("Specified join keys do not exist in the files.")
        if left_cols[left_key].get("hidden") or right_cols[right_key].get("hidden"):
            raise ValidationError("Hidden columns cannot be used as connection keys.")

        left_col = left_cols[left_key]
        right_col = right_cols[right_key]
        if not check_type_compatibility(left_col.get("type", "text"), right_col.get("type", "text")):
            raise ValidationError("Join keys must have the same type family (text/text or number/number; date keys not allowed).")

        return left_file, right_file, left_col, right_col

    async def preview(
        self, company_id: str, left_file_id: str, left_key: str, right_file_id: str, right_key: str
    ) -> Dict[str, Any]:
        _, right_file, left_col, right_col = await self._validate_connection_parties(
            company_id, left_file_id, left_key, right_file_id, right_key
        )
        right_type = right_col.get("type", "text")
        left_type = left_col.get("type", "text")

        raw_right_vals = await self.conn_repo.stream_raw_column_values(company_id, right_file_id, right_key)
        is_unique, unique_right_keys, duplicate_samples = evaluate_right_key_uniqueness(raw_right_vals, right_type)
        if not is_unique:
            return {
                "ok": False,
                "right_unique": False,
                "duplicate_samples": duplicate_samples,
                "match_rate": 0.0,
                "matched": 0,
                "left_rows": 0,
                "right_rows": len(raw_right_vals),
                "unmatched_samples": [],
                "warning": False,
            }

        raw_left_vals = await self.conn_repo.stream_raw_column_values(company_id, left_file_id, left_key)
        match_rate, matched, left_rows, unmatched_samples = calculate_match_metrics(
            raw_left_vals, unique_right_keys, left_type
        )
        warning = match_rate < settings.CONNECTION_WARN_MATCH_RATE
        return {
            "ok": True,
            "match_rate": match_rate,
            "matched": matched,
            "left_rows": left_rows,
            "right_rows": len(raw_right_vals),
            "right_unique": True,
            "duplicate_samples": [],
            "unmatched_samples": unmatched_samples,
            "warning": warning,
        }

    async def create_connection(
        self, company_id: str, user_id: str, left_file_id: str, left_key: str, right_file_id: str, right_key: str, name: Optional[str] = None
    ) -> Dict[str, Any]:
        count = await self.conn_repo.count_connections(company_id)
        if count >= settings.CONNECTIONS_MAX_PER_COMPANY:
            raise AppError("CONNECTIONS_LIMIT_REACHED", f"Limit of {settings.CONNECTIONS_MAX_PER_COMPANY} connections reached.", 422)

        left_file, right_file, left_col, right_col = await self._validate_connection_parties(
            company_id, left_file_id, left_key, right_file_id, right_key
        )
        alias = right_file.get("slug", "")
        if not is_valid_slug(alias):
            raise ValidationError(f"Target file slug '{alias}' is invalid as alias.")

        preview_res = await self.preview(company_id, left_file_id, left_key, right_file_id, right_key)
        if not preview_res["right_unique"]:
            raise AppError("NOT_UNIQUE", "Right key has duplicate values.", 422, {"duplicate_samples": preview_res["duplicate_samples"]})
        if preview_res["match_rate"] < settings.CONNECTION_MIN_MATCH_RATE:
            raise AppError("LOW_MATCH_RATE", f"Match rate {preview_res['match_rate']} is below minimum threshold.", 422)

        conn_name = name.strip() if name and name.strip() else f"{left_file.get('name')} → {right_file.get('name')}"
        existing = await self.conn_repo.find_existing(company_id, left_file_id, left_key, right_file_id, right_key)
        if existing:
            conn_id = str(existing["_id"])
            await self.conn_repo.update_connection(company_id, conn_id, {"status": "refreshing", "name": conn_name})
        else:
            doc = create_connection_document(
                company_id=company_id,
                name=conn_name,
                left_file_id=left_file_id,
                left_key=left_key,
                right_file_id=right_file_id,
                right_key=right_key,
                alias=alias,
                status="refreshing",
                match_rate=preview_res["match_rate"],
                matched_rows=preview_res["matched"],
                left_rows=preview_res["left_rows"],
                right_rows=preview_res["right_rows"],
                created_by=user_id,
            )
            conn_id = await self.conn_repo.create_connection(doc)

        asyncio.create_task(self.worker.run_enrichment(company_id, conn_id))
        return {"id": conn_id, "status": "refreshing"}

    async def list_connections(self, company_id: str) -> Dict[str, Any]:
        docs = await self.conn_repo.list_connections(company_id)
        files = {str(f["_id"]): f.get("name", "") for f in await self.files_repo.list_files(company_id)}
        res = []
        for d in docs:
            lid = d.get("left_file_id", "")
            rid = d.get("right_file_id", "")
            res.append({
                "id": str(d["_id"]),
                "name": d.get("name", ""),
                "left": {"file_id": lid, "file_name": files.get(lid, "Unknown"), "key": d.get("left_key", "")},
                "right": {"file_id": rid, "file_name": files.get(rid, "Unknown"), "key": d.get("right_key", "")},
                "alias": d.get("alias", ""),
                "status": d.get("status", "suggested"),
                "match_rate": d.get("match_rate", 0.0),
                "updated_at": d.get("updated_at"),
            })
        return {"connections": res}

    async def accept_suggestion(self, company_id: str, user_id: str, conn_id: str) -> Dict[str, Any]:
        conn = await self.conn_repo.get_connection_by_id(company_id, conn_id)
        if not conn:
            raise NotFoundError("Connection not found.")
        await self.conn_repo.update_connection(company_id, conn_id, {"status": "refreshing"})
        asyncio.create_task(self.worker.run_enrichment(company_id, conn_id))
        return {"id": conn_id, "status": "refreshing"}

    async def dismiss_suggestion(self, company_id: str, user_id: str, conn_id: str) -> Dict[str, Any]:
        conn = await self.conn_repo.get_connection_by_id(company_id, conn_id)
        if not conn:
            raise NotFoundError("Connection not found.")
        await self.conn_repo.update_connection(company_id, conn_id, {"status": "dismissed"})
        return {"id": conn_id, "status": "dismissed"}

    async def refresh_connection(self, company_id: str, user_id: str, conn_id: str) -> Dict[str, Any]:
        conn = await self.conn_repo.get_connection_by_id(company_id, conn_id)
        if not conn:
            raise NotFoundError("Connection not found.")
        await self.conn_repo.update_connection(company_id, conn_id, {"status": "refreshing"})
        asyncio.create_task(self.worker.run_enrichment(company_id, conn_id))
        return {"id": conn_id, "status": "refreshing"}

    async def delete_connection(self, company_id: str, user_id: str, conn_id: str) -> None:
        conn = await self.conn_repo.get_connection_by_id(company_id, conn_id)
        if not conn:
            raise NotFoundError("Connection not found.")
        left_file_id = conn.get("left_file_id", "")
        alias = conn.get("alias", "")
        await self.conn_repo.delete_connection(company_id, conn_id)
        asyncio.create_task(self.conn_repo.unset_joined_alias(company_id, left_file_id, alias))

    async def generate_suggestions(self, company_id: str) -> None:
        files = await self.files_repo.list_files(company_id)
        ready_files = [f for f in files if f.get("status") == "ready"]
        if len(ready_files) < 2:
            return

        for left in ready_files:
            left_id = str(left["_id"])
            for right in ready_files:
                right_id = str(right["_id"])
                if left_id == right_id:
                    continue
                right_slug = right.get("slug", "")
                left_cols = [c for c in left.get("columns", []) if not c.get("hidden")]
                right_cols = [c for c in right.get("columns", []) if not c.get("hidden")]

                for lc in left_cols:
                    lk = lc["key"]
                    for rc in right_cols:
                        rk = rc["key"]
                        if not (lk == rk or lk == f"{right_slug}_{rk}" or rk == f"{left.get('slug', '')}_{lk}"):
                            continue
                        if not check_type_compatibility(lc.get("type", "text"), rc.get("type", "text")):
                            continue

                        existing = await self.conn_repo.find_existing(company_id, left_id, lk, right_id, rk)
                        if existing:
                            continue

                        prev = await self.preview(company_id, left_id, lk, right_id, rk)
                        if prev.get("ok") and prev.get("right_unique") and prev.get("match_rate", 0.0) >= settings.CONNECTION_SUGGEST_MIN_MATCH_RATE:
                            name = f"{left.get('name')} → {right.get('name')}"
                            doc = create_connection_document(
                                company_id=company_id,
                                name=name,
                                left_file_id=left_id,
                                left_key=lk,
                                right_file_id=right_id,
                                right_key=rk,
                                alias=right_slug,
                                status="suggested",
                                match_rate=prev["match_rate"],
                                matched_rows=prev["matched"],
                                left_rows=prev["left_rows"],
                                right_rows=prev["right_rows"],
                            )
                            await self.conn_repo.create_connection(doc)
