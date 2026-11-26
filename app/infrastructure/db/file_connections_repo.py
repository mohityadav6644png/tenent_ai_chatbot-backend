"""
File Connections Mongo Repository (app/infrastructure/db/file_connections_repo.py)
Persistence for file_connections (App DB) and joined data enrichment on company_rows (Business DB).
"""

from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Set
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, UpdateOne

from app.infrastructure.db.mongodb import (
    app_database,
    business_database_reader,
    business_database_writer,
    file_connections_collection,
)
from app.modules.file_connections.validation import normalize_key_value


class MongoFileConnectionsRepository:
    def __init__(self, app_db: Any = None, writer_db: Any = None, reader_db: Any = None):
        self._app_db = app_db if app_db is not None else app_database
        self._writer_db = writer_db if writer_db is not None else business_database_writer
        self._reader_db = reader_db if reader_db is not None else business_database_reader

    @property
    def conn_coll(self):
        return self._app_db["file_connections"] if self._app_db is not None else file_connections_collection

    @property
    def rows_writer(self):
        return self._writer_db["company_rows"]

    @property
    def rows_reader(self):
        return self._reader_db["company_rows"]

    async def list_connections(self, company_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn_coll.find({"company_id": str(company_id)}).sort("updated_at", DESCENDING)
        return await cursor.to_list(length=100)

    async def get_connection_by_id(self, company_id: str, conn_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(conn_id) if ObjectId.is_valid(conn_id) else conn_id
        return await self.conn_coll.find_one({"company_id": str(company_id), "_id": oid})

    async def find_existing(
        self, company_id: str, left_file_id: str, left_key: str, right_file_id: str, right_key: str
    ) -> Optional[Dict[str, Any]]:
        return await self.conn_coll.find_one({
            "company_id": str(company_id),
            "left_file_id": str(left_file_id),
            "left_key": str(left_key),
            "right_file_id": str(right_file_id),
            "right_key": str(right_key),
        })

    async def count_connections(self, company_id: str) -> int:
        return await self.conn_coll.count_documents({
            "company_id": str(company_id),
            "status": {"$in": ["suggested", "refreshing", "active", "needs_attention"]},
        })

    async def create_connection(self, doc: Dict[str, Any]) -> str:
        res = await self.conn_coll.insert_one(doc)
        return str(res.inserted_id)

    async def update_connection(self, company_id: str, conn_id: str, updates: Dict[str, Any]) -> None:
        oid = ObjectId(conn_id) if ObjectId.is_valid(conn_id) else conn_id
        updates["updated_at"] = datetime.now(timezone.utc)
        await self.conn_coll.update_one({"company_id": str(company_id), "_id": oid}, {"$set": updates})

    async def delete_connection(self, company_id: str, conn_id: str) -> None:
        oid = ObjectId(conn_id) if ObjectId.is_valid(conn_id) else conn_id
        await self.conn_coll.delete_one({"company_id": str(company_id), "_id": oid})

    async def get_connections_by_file(self, company_id: str, file_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn_coll.find({
            "company_id": str(company_id),
            "$or": [{"left_file_id": str(file_id)}, {"right_file_id": str(file_id)}],
        })
        return await cursor.to_list(length=100)

    async def stream_raw_column_values(self, company_id: str, file_id: str, column_key: str) -> List[Any]:
        doc_field = f"data.{column_key}"
        cursor = self.rows_reader.find(
            {"company_id": str(company_id), "file_id": str(file_id), doc_field: {"$ne": None}},
            {doc_field: 1},
        )
        values = []
        async for doc in cursor:
            d = doc.get("data", {})
            if column_key in d and d[column_key] is not None:
                values.append(d[column_key])
        return values

    async def stream_right_rows_dict(
        self,
        company_id: str,
        file_id: str,
        key: str,
        key_type: str,
        hidden_keys: Set[str],
        level: int = 0,
    ) -> Dict[Any, Dict[str, Any]]:
        cursor = self.rows_reader.find(
            {"company_id": str(company_id), "file_id": str(file_id)},
            {"data": 1},
        )
        right_map: Dict[Any, Dict[str, Any]] = {}
        async for doc in cursor:
            data = doc.get("data", {})
            raw_key = data.get(key)
            norm = normalize_key_value(raw_key, key_type, level=level)
            if norm is None:
                continue
            # Keep only non-hidden fields, excluding raw MongoDB _id
            filtered = {k: v for k, v in data.items() if k not in hidden_keys and k != "_id"}
            right_map[norm] = filtered
        return right_map

    async def stream_left_rows_batch(
        self, company_id: str, file_id: str, batch_size: int = 1000
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        cursor = self.rows_reader.find(
            {"company_id": str(company_id), "file_id": str(file_id)},
            {"_id": 1, "data": 1},
        )
        batch: List[Dict[str, Any]] = []
        async for doc in cursor:
            batch.append(doc)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    async def bulk_update_joined(
        self, company_id: str, file_id: str, set_ops: List[Dict[str, Any]], unset_ids: List[str], alias: str
    ) -> None:
        ops = []
        for item in set_ops:
            ops.append(UpdateOne(
                {"_id": item["_id"], "company_id": str(company_id), "file_id": str(file_id)},
                {"$set": {f"joined.{alias}": item["data"]}},
            ))
        for uid in unset_ids:
            ops.append(UpdateOne(
                {"_id": uid, "company_id": str(company_id), "file_id": str(file_id)},
                {"$unset": {f"joined.{alias}": ""}},
            ))
        if ops:
            await self.rows_writer.bulk_write(ops, ordered=False)

    async def unset_joined_alias(self, company_id: str, file_id: str, alias: str) -> None:
        await self.rows_writer.update_many(
            {"company_id": str(company_id), "file_id": str(file_id)},
            {"$unset": {f"joined.{alias}": ""}},
        )
