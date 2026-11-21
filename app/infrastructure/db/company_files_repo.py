"""
Company Files Repository (app/infrastructure/db/company_files_repo.py)
Persistence and aggregation for company_files (App DB) and generic company_rows (Business DB).
Strictly separates business writer for ingestion and business reader for stats/queries.
"""

from datetime import datetime, timedelta, timezone
import inspect
import re
from typing import Any, Dict, List, Optional
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from app.infrastructure.db.mongodb import (
    app_database,
    business_database_reader,
    business_database_writer,
    company_files_collection,
)


class MongoCompanyFilesRepository:
    def __init__(
        self,
        app_db: Any = None,
        writer_db: Any = None,
        reader_db: Any = None,
    ):
        self._app_db = app_db if app_db is not None else app_database
        self._writer_db = writer_db if writer_db is not None else business_database_writer
        self._reader_db = reader_db if reader_db is not None else business_database_reader

    @property
    def files_coll(self):
        return self._app_db["company_files"]

    @property
    def rows_writer(self):
        return self._writer_db["company_rows"]

    @property
    def rows_reader(self):
        return self._reader_db["company_rows"]

    async def list_files(self, company_id: str) -> List[Dict[str, Any]]:
        cursor = self.files_coll.find(
            {"company_id": str(company_id)}
        ).sort("updated_at", DESCENDING)
        return await cursor.to_list(length=100)

    async def get_file_by_id(self, company_id: str, file_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(file_id) if ObjectId.is_valid(file_id) else file_id
        return await self.files_coll.find_one({"company_id": str(company_id), "_id": oid})

    async def get_file_by_slug(self, company_id: str, slug: str) -> Optional[Dict[str, Any]]:
        return await self.files_coll.find_one(
            {"company_id": str(company_id), "slug": slug.strip().lower()}
        )

    async def get_file_by_name(self, company_id: str, name: str) -> Optional[Dict[str, Any]]:
        return await self.files_coll.find_one({
            "company_id": str(company_id),
            "name": {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"},
        })

    async def count_files(self, company_id: str) -> int:
        return await self.files_coll.count_documents({
            "company_id": str(company_id),
            "status": {"$in": ["ready", "processing"]},
        })

    async def create_file_record(self, doc: Dict[str, Any]) -> str:
        res = await self.files_coll.insert_one(doc)
        return str(res.inserted_id)

    async def update_file_record(self, file_id: str, updates: Dict[str, Any]) -> None:
        oid = ObjectId(file_id) if ObjectId.is_valid(file_id) else file_id
        updates["updated_at"] = datetime.now(timezone.utc)
        await self.files_coll.update_one({"_id": oid}, {"$set": updates})

    async def delete_file_record(self, company_id: str, file_id: str) -> None:
        oid = ObjectId(file_id) if ObjectId.is_valid(file_id) else file_id
        await self.files_coll.delete_one({"company_id": str(company_id), "_id": oid})

    async def insert_rows_batch(self, docs_batch: List[Dict[str, Any]]) -> None:
        if docs_batch:
            await self.rows_writer.insert_many(docs_batch, ordered=False)

    async def delete_rows_by_import(self, company_id: str, file_id: str, import_id: str) -> None:
        await self.rows_writer.delete_many({
            "company_id": str(company_id),
            "file_id": str(file_id),
            "import_id": str(import_id),
        })

    async def delete_old_rows(self, company_id: str, file_id: str, keep_import_id: str) -> None:
        await self.rows_writer.delete_many({
            "company_id": str(company_id),
            "file_id": str(file_id),
            "import_id": {"$ne": str(keep_import_id)},
        })

    async def delete_all_file_rows(self, company_id: str, file_id: str) -> None:
        await self.rows_writer.delete_many({
            "company_id": str(company_id),
            "file_id": str(file_id),
        })

    async def count_file_rows(self, company_id: str, file_id: str) -> int:
        return await self.rows_reader.count_documents({
            "company_id": str(company_id),
            "file_id": str(file_id),
        })

    async def get_active_import(self, company_id: str) -> Optional[Dict[str, Any]]:
        threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
        return await self.files_coll.find_one({
            "company_id": str(company_id),
            "status": "processing",
            "updated_at": {"$gte": threshold},
        })

    async def compute_file_stats(
        self,
        company_id: str,
        file_id: str,
        columns: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Stats per column, computed with company-scoped aggregation, never logged:
        row_count; date columns min/max; number columns min/max;
        text columns with at most 20 distinct values -> list (each cut to 40 chars).
        Hidden columns get NO stats.
        """
        total_rows = await self.count_file_rows(company_id, file_id)
        stats: Dict[str, Any] = {"row_count": total_rows, "columns": {}}

        non_hidden_cols = [c for c in columns if not c.get("hidden")]

        for col in non_hidden_cols:
            k = col["key"]
            c_type = col.get("type", "text")
            doc_field = f"data.{k}"
            expr_field = f"$data.{k}"

            if c_type in ("number", "date"):
                pipeline = [
                    {"$match": {"company_id": str(company_id), "file_id": str(file_id), doc_field: {"$ne": None}}},
                    {"$group": {
                        "_id": None,
                        "min": {"$min": expr_field},
                        "max": {"$max": expr_field},
                    }},
                ]
                agg_res = self.rows_reader.aggregate(pipeline)
                cursor = await agg_res if inspect.isawaitable(agg_res) else agg_res
                res = await cursor.to_list(1)
                if res and res[0]:
                    min_val = res[0].get("min")
                    max_val = res[0].get("max")
                    if isinstance(min_val, datetime):
                        min_val = min_val.isoformat()
                    if isinstance(max_val, datetime):
                        max_val = max_val.isoformat()
                    stats["columns"][k] = {"min": min_val, "max": max_val}
                else:
                    stats["columns"][k] = {"min": None, "max": None}

            elif c_type == "text":
                pipeline = [
                    {"$match": {"company_id": str(company_id), "file_id": str(file_id), doc_field: {"$ne": None}}},
                    {"$group": {"_id": expr_field}},
                    {"$limit": 21},
                ]
                agg_res = self.rows_reader.aggregate(pipeline)
                cursor = await agg_res if inspect.isawaitable(agg_res) else agg_res
                distinct_res = await cursor.to_list(21)
                if len(distinct_res) <= 20:
                    vals = [
                        str(d["_id"])[:40] for d in distinct_res if d["_id"] is not None and str(d["_id"]).strip()
                    ]
                    stats["columns"][k] = {"distinct_values": vals}
                else:
                    stats["columns"][k] = {"distinct_values": None}

            elif c_type == "boolean":
                stats["columns"][k] = {"distinct_values": [True, False]}

        return stats
