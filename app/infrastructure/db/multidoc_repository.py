"""
Mongo Multi-document Repository (app/infrastructure/db/multidoc_repository.py)
Persistence adapter for tenant-scoped company files and rows.
Enforces company_id boundary and strips hidden columns.
"""

from typing import Any, Dict, List, Optional, Set
from bson import ObjectId
from app.infrastructure.db.mongodb import app_database, business_database_reader
from app.modules.multidoc.interfaces import MultiDocRepositoryProtocol
from app.modules.multidoc.models import DocumentSummary


class MongoMultiDocRepository(MultiDocRepositoryProtocol):
    def __init__(self, app_db: Any = None, reader_db: Any = None):
        self._app_db = app_db if app_db is not None else app_database
        self._reader_db = reader_db if reader_db is not None else business_database_reader

    @property
    def files_coll(self):
        return self._app_db["company_files"]

    @property
    def rows_coll(self):
        return self._reader_db["company_rows"]

    async def list_documents(
        self, company_id: str, limit: int = 6
    ) -> List[DocumentSummary]:
        if not company_id or not str(company_id).strip():
            return []

        cursor = self.files_coll.find(
            {"company_id": str(company_id), "status": "ready"}
        ).sort("updated_at", -1).limit(limit)

        docs = await cursor.to_list(length=limit)
        summaries: List[DocumentSummary] = []
        for d in docs:
            non_hidden_fields = [
                c.get("key") for c in d.get("columns", [])
                if not c.get("hidden") and c.get("key")
            ]
            summaries.append(
                DocumentSummary(
                    document_id=str(d.get("_id") or d.get("slug")),
                    source_name=d.get("name") or d.get("slug") or "Untitled",
                    record_count=d.get("row_count", 0),
                    fields=non_hidden_fields,
                )
            )
        return summaries

    async def get_document_by_id_or_slug(
        self, company_id: str, doc_id_or_slug: str
    ) -> Optional[Dict[str, Any]]:
        if not company_id or not doc_id_or_slug:
            return None

        oid = ObjectId(doc_id_or_slug) if ObjectId.is_valid(doc_id_or_slug) else None
        query = {"company_id": str(company_id)}
        if oid:
            query["$or"] = [{"_id": oid}, {"slug": doc_id_or_slug}]
        else:
            query["slug"] = doc_id_or_slug

        return await self.files_coll.find_one(query)

    async def get_document_records(
        self,
        company_id: str,
        document_id: str,
        limit: int = 200,
        hidden_keys: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not company_id or not document_id:
            return []

        file_doc = await self.get_document_by_id_or_slug(company_id, document_id)
        if not file_doc:
            return []

        real_file_id = str(file_doc["_id"])
        file_hidden = {
            c["key"] for c in file_doc.get("columns", []) if c.get("hidden") and "key" in c
        }
        all_hidden = file_hidden | (hidden_keys or set())

        cursor = self.rows_coll.find(
            {"company_id": str(company_id), "file_id": real_file_id}
        ).limit(limit)

        rows = await cursor.to_list(length=limit)
        results: List[Dict[str, Any]] = []
        for r in rows:
            data = dict(r.get("data", {}))
            for hk in all_hidden:
                data.pop(hk, None)
            data.pop("_id", None)
            data.pop("company_id", None)
            results.append(data)

        return results
