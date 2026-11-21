"""
Query Executor (app/infrastructure/db/executor.py)
QueryExecutor now runs MongoDB aggregation pipelines on business_data.
Provides MongoExecutor and scope_pipeline choke point for tenant isolation.
"""

from datetime import datetime
import inspect
import json
from typing import Any, Dict, List, Optional, Tuple
from bson import Decimal128, ObjectId
from app.core.config import settings
from app.core.context import TenantContext
from app.core.exceptions import InternalError, QueryTimeoutError, UnauthenticatedError
from app.core.logging import logger
from app.infrastructure.db.mongodb import get_business_collection


def scope_pipeline(ctx: Any, pipeline: List[Dict[str, Any]], max_rows: int = 100) -> List[Dict[str, Any]]:
    """Injected $match choke point: ensures every query is partitioned by company_id."""
    company_id = getattr(ctx, "company_id", None) if hasattr(ctx, "company_id") else ctx
    if not company_id or not str(company_id).strip():
        raise UnauthenticatedError("Tenant context is missing company_id.")
    return [{"$match": {"company_id": str(company_id)}}] + list(pipeline) + [{"$limit": max_rows + 1}]


def scope_file_pipeline(
    ctx: Any, file_id: str, hidden_keys: List[str], pipeline: List[Dict[str, Any]], max_rows: int = 100
) -> List[Dict[str, Any]]:
    """Choke point for company files: partitions by company_id and file_id, replaces root with data, and unsets hidden keys."""
    company_id = getattr(ctx, "company_id", None) if hasattr(ctx, "company_id") else ctx
    if not company_id or not str(company_id).strip():
        raise UnauthenticatedError("Tenant context is missing company_id.")
    prefix: List[Dict[str, Any]] = [
        {"$match": {"company_id": str(company_id), "file_id": str(file_id)}},
        {"$replaceRoot": {"newRoot": "$data"}},
    ]
    if hidden_keys:
        prefix.append({"$unset": list(hidden_keys)})
    return prefix + list(pipeline) + [{"$limit": max_rows + 1}]


def _convert_extended_json(val: Any) -> Any:
    if isinstance(val, dict):
        if len(val) == 1 and "$date" in val:
            d_str = val["$date"]
            if isinstance(d_str, str):
                return datetime.fromisoformat(d_str.replace("Z", "+00:00"))
        return {k: _convert_extended_json(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_convert_extended_json(item) for item in val]
    return val


def _to_scalar(val: Any) -> Any:
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, ObjectId):
        return str(val)
    if isinstance(val, Decimal128):
        return float(val.to_decimal())
    return val


class MongoExecutor:
    """Executes validated MongoDB aggregation pipelines under strict tenant scoping."""

    def __init__(self, max_rows: int = 100, timeout_seconds: int = 5, db: Any = None, app_db: Any = None):
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self._db = db
        self._app_db = app_db

    async def execute_query(
        self, query: Any, company_id: str, columns: Optional[List[str]] = None
    ) -> Tuple[List[Dict[str, str]], List[List[Any]], bool]:
        if not company_id or not str(company_id).strip():
            raise UnauthenticatedError("Query execution requires a non-empty company_id.")

        if isinstance(query, str):
            query = json.loads(query)

        collection_name = query["collection"]
        pipeline = query["pipeline"]

        # Check if collection is one of the fixed collections or a company file slug
        fixed_collections = {"customers", "products", "orders", "invoices", "stock_levels"}
        if collection_name in fixed_collections:
            scoped = scope_pipeline(company_id, pipeline, self.max_rows)
            scoped = _convert_extended_json(scoped)

            if self._db is not None:
                coll = self._db[collection_name]
            else:
                ctx = TenantContext(user_id="system", company_id=str(company_id), role="employee")
                coll = get_business_collection(ctx, collection_name)
        else:
            # Company file resolution from (company_id, slug)
            from app.infrastructure.db.mongodb import company_files_collection

            files_coll = self._app_db["company_files"] if self._app_db is not None else company_files_collection
            file_doc = await files_coll.find_one({
                "company_id": str(company_id),
                "slug": collection_name.strip().lower(),
            })
            if not file_doc:
                oid = ObjectId(collection_name) if ObjectId.is_valid(collection_name) else collection_name
                file_doc = await files_coll.find_one({
                    "company_id": str(company_id),
                    "_id": oid,
                })

            if not file_doc:
                raise InternalError(f"Company file '{collection_name}' not found for tenant.")

            file_id = str(file_doc["_id"])
            hidden_keys = [c["key"] for c in file_doc.get("columns", []) if c.get("hidden")]

            scoped = scope_file_pipeline(company_id, file_id, hidden_keys, pipeline, self.max_rows)
            scoped = _convert_extended_json(scoped)

            if self._db is not None:
                coll = self._db["company_rows"]
            else:
                ctx = TenantContext(user_id="system", company_id=str(company_id), role="employee")
                coll = get_business_collection(ctx, "company_rows")

        timeout_ms = int(self.timeout_seconds * 1000)
        try:
            res = coll.aggregate(scoped, maxTimeMS=timeout_ms, allowDiskUse=False, batchSize=self.max_rows + 1)
            cursor = await res if inspect.isawaitable(res) else res
            raw_docs = await cursor.to_list(length=self.max_rows + 1)
        except Exception as exc:
            err_msg = str(exc).lower()
            if "timelimitexceeded" in err_msg or "timeout" in err_msg:
                raise QueryTimeoutError("Query execution exceeded timeout limit.")
            logger.error(f"MongoDB aggregation execution failed: {exc}")
            raise InternalError("Database query execution failed.")

        truncated = len(raw_docs) > self.max_rows
        docs = raw_docs[: self.max_rows]

        col_names = columns or (list(docs[0].keys()) if docs else [])
        col_defs = [{"name": c, "type": "string"} for c in col_names]
        rows = [[_to_scalar(d.get(c)) for c in col_names] for d in docs]
        return col_defs, rows, truncated


class PostgresExecutor:
    """Legacy executor retained for backward compatibility."""

    def __init__(self, max_rows: int = 100, timeout_seconds: int = 5):
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds

    async def execute_query(self, sql: str, company_id: str):
        return [], [], False
