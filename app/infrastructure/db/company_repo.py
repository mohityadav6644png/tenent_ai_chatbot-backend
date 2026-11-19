"""
Company MongoDB Repositories (app/infrastructure/db/company_repo.py)
Ingestion, records, and import-tracking repositories implementing module interfaces.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, UpdateOne
from pymongo.errors import BulkWriteError

from app.infrastructure.db.mongodb import (
    app_database,
    business_database_reader,
    business_database_writer,
    company_records_collection,
)


class MongoCompanyRecordsRepository:
    """Repository for tenant text records stored in app_database company_records collection."""

    def __init__(self, db: Any = None):
        self._db = db

    def _get_collection(self):
        if self._db is not None:
            return self._db["company_records"]
        return company_records_collection

    async def insert_record(self, doc: Dict[str, Any]) -> str:
        coll = self._get_collection()
        res = await coll.insert_one(doc)
        return str(res.inserted_id)

    async def bulk_insert_records(self, docs: List[Dict[str, Any]]) -> int:
        if not docs:
            return 0
        coll = self._get_collection()
        batch_size = 1000
        total_inserted = 0
        for i in range(0, len(docs), batch_size):
            batch = docs[i : i + batch_size]
            try:
                res = await coll.insert_many(batch, ordered=False)
                total_inserted += len(res.inserted_ids)
            except BulkWriteError as bwe:
                total_inserted += bwe.details.get("nInserted", 0)
        return total_inserted


class MongoDataIngestionRepository:
    """Repository for structured business datasets using business_database_writer."""

    def __init__(self, writer_db: Any = None, reader_db: Any = None):
        self._writer_db = writer_db or business_database_writer
        self._reader_db = reader_db or business_database_reader

    async def get_dataset_stats(
        self, company_id: str, collection_name: str
    ) -> Tuple[int, Optional[datetime]]:
        coll = self._reader_db[collection_name]
        query = {"company_id": str(company_id)}
        count = await coll.count_documents(query)
        if count == 0:
            return 0, None

        # Determine most recent timestamp across possible date fields
        latest_dt: Optional[datetime] = None
        for date_col in ("updated_at", "order_date", "invoice_date", "created_at", "last_counted_at"):
            cursor = coll.find({**query, date_col: {"$exists": True, "$ne": None}}).sort(date_col, DESCENDING).limit(1)
            docs = await cursor.to_list(1)
            if docs and docs[0].get(date_col):
                val = docs[0][date_col]
                if isinstance(val, datetime):
                    if latest_dt is None or val > latest_dt:
                        latest_dt = val
                elif isinstance(val, str):
                    try:
                        parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
                        if latest_dt is None or parsed > latest_dt:
                            latest_dt = parsed
                    except Exception:
                        pass
        return count, latest_dt

    async def upsert_records(
        self,
        company_id: str,
        collection_name: str,
        natural_key: List[str],
        records: List[Dict[str, Any]],
    ) -> Tuple[int, int]:
        if not records:
            return 0, 0

        coll = self._writer_db[collection_name]
        now = datetime.now(UTC)
        operations = []

        for rec in records:
            doc = dict(rec)
            filter_query: Dict[str, Any] = {"company_id": str(company_id)}
            for k in natural_key:
                filter_query[k] = doc.get(k)

            # Ensure company_id and updated_at
            doc["company_id"] = str(company_id)
            doc["updated_at"] = now

            operations.append(
                UpdateOne(
                    filter_query,
                    {
                        "$set": doc,
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            )

        batch_size = 1000
        inserted_count = 0
        updated_count = 0

        for i in range(0, len(operations), batch_size):
            batch = operations[i : i + batch_size]
            res = await coll.bulk_write(batch, ordered=False)
            inserted_count += res.upserted_count
            updated_count += res.modified_count

        return inserted_count, updated_count

    async def replace_records(
        self,
        company_id: str,
        collection_name: str,
        import_id: str,
        records: List[Dict[str, Any]],
    ) -> int:
        if not records:
            return 0

        coll = self._writer_db[collection_name]
        now = datetime.now(UTC)
        prepared_docs = []

        for rec in records:
            doc = dict(rec)
            doc["company_id"] = str(company_id)
            doc["import_id"] = import_id
            doc["created_at"] = now
            doc["updated_at"] = now
            prepared_docs.append(doc)

        batch_size = 1000
        inserted_total = 0

        try:
            for i in range(0, len(prepared_docs), batch_size):
                batch = prepared_docs[i : i + batch_size]
                res = await coll.insert_many(batch, ordered=False)
                inserted_total += len(res.inserted_ids)

            # Successfully inserted new generation; delete older generations for this company
            await coll.delete_many(
                {
                    "company_id": str(company_id),
                    "import_id": {"$ne": import_id},
                }
            )
            return inserted_total
        except Exception:
            # Rollback: Clean up newly inserted documents on error
            await coll.delete_many(
                {
                    "company_id": str(company_id),
                    "import_id": import_id,
                }
            )
            raise


class MongoDataImportRepository:
    """Repository for import audit history and concurrency tracking in app_database."""

    def __init__(self, db: Any = None):
        self._db = db or app_database
        self._collection = self._db["data_imports"]

    async def has_active_import(
        self, company_id: str, dataset: str, timeout_minutes: int = 15
    ) -> bool:
        cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
        query = {
            "company_id": str(company_id),
            "dataset": dataset,
            "status": "in_progress",
            "started_at": {"$gte": cutoff},
        }
        doc = await self._collection.find_one(query)
        return doc is not None

    async def count_recent_imports(self, company_id: str, hours: int = 1) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        query = {
            "company_id": str(company_id),
            "started_at": {"$gte": cutoff},
        }
        return await self._collection.count_documents(query)

    async def create_import(self, doc: Dict[str, Any]) -> str:
        res = await self._collection.insert_one(doc)
        return str(res.inserted_id)

    async def update_import(self, import_id: str, updates: Dict[str, Any]) -> None:
        await self._collection.update_one(
            {"import_id": import_id},
            {"$set": updates},
        )
