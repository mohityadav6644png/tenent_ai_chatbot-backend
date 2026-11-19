"""
Dataset Understanding & Summary Service (app/modules/company/summary.py)
Computes and stores per-tenant dataset summaries with field statistics, min/max ranges,
and distinct values under strict company_id scoping.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from pymongo import DESCENDING

from app.infrastructure.db.mongodb import (
    app_database,
    business_database_reader,
    dataset_catalog_collection,
)
from app.modules.company.datasets import DATASET_CATALOG, get_dataset_definition

MAX_CATALOG_SUMMARY_BYTES = 15360  # 15 KB cap


async def build_dataset_summary(
    company_id: str,
    dataset_key: str,
    reader_db: Any = None,
    app_db: Any = None,
) -> Dict[str, Any]:
    """
    Computes field statistics, min/max values, and distinct categorical values
    for a specific tenant and dataset, and stores the summary in the app database.
    """
    definition = get_dataset_definition(dataset_key)
    if not definition:
        return {}

    r_db = reader_db or business_database_reader
    a_coll = (app_db["dataset_catalog"] if app_db is not None else dataset_catalog_collection)
    coll = r_db[definition.collection_name]

    query = {"company_id": str(company_id)}
    row_count = await coll.count_documents(query)

    summary_doc: Dict[str, Any] = {
        "company_id": str(company_id),
        "dataset": dataset_key,
        "collection_name": definition.collection_name,
        "row_count": row_count,
        "last_imported_at": None,
        "fields": [],
        "updated_at": datetime.now(timezone.utc),
    }

    if row_count == 0:
        await a_coll.update_one(
            {"company_id": str(company_id), "dataset": dataset_key},
            {"$set": summary_doc},
            upsert=True,
        )
        return summary_doc

    # Determine latest imported/updated timestamp
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

    summary_doc["last_imported_at"] = latest_dt.isoformat() if latest_dt else None

    # Inspect each field defined for the dataset
    fields_list = []
    for col_name in definition.all_columns:
        col_type = definition.field_types.get(col_name, "string")
        desc = definition.field_descriptions.get(col_name, f"{col_name} field")
        is_queryable = definition.is_queryable(col_name)

        f_meta: Dict[str, Any] = {
            "name": col_name,
            "type": col_type,
            "description": desc,
            "queryable": is_queryable,
        }

        # Personal / non-queryable fields do NOT store min/max or distinct values
        if not is_queryable:
            fields_list.append(f_meta)
            continue

        # For queryable date fields: compute min and max
        if col_type == "date":
            pipeline = [
                {"$match": {**query, col_name: {"$exists": True, "$ne": None}}},
                {"$group": {"_id": None, "min": {"$min": f"${col_name}"}, "max": {"$max": f"${col_name}"}}},
            ]
            try:
                res_cursor = await coll.aggregate(pipeline)
                docs = await res_cursor.to_list(1)
                if docs:
                    min_val = docs[0].get("min")
                    max_val = docs[0].get("max")
                    f_meta["min"] = min_val.isoformat() if isinstance(min_val, datetime) else str(min_val) if min_val else None
                    f_meta["max"] = max_val.isoformat() if isinstance(max_val, datetime) else str(max_val) if max_val else None
            except Exception:
                pass

        # For queryable numeric fields: compute min and max
        elif col_type in ("integer", "float", "number"):
            pipeline = [
                {"$match": {**query, col_name: {"$exists": True, "$ne": None}}},
                {"$group": {"_id": None, "min": {"$min": f"${col_name}"}, "max": {"$max": f"${col_name}"}}},
            ]
            try:
                res_cursor = await coll.aggregate(pipeline)
                docs = await res_cursor.to_list(1)
                if docs:
                    f_meta["min"] = docs[0].get("min")
                    f_meta["max"] = docs[0].get("max")
            except Exception:
                pass

        # For queryable string fields: collect distinct values (if <= 20 distinct)
        elif col_type in ("string", "text"):
            try:
                distinct_vals = await coll.distinct(col_name, {**query, col_name: {"$exists": True, "$ne": None, "$ne": ""}})
                if distinct_vals and len(distinct_vals) <= 20:
                    f_meta["distinct_values"] = [str(v)[:40] for v in distinct_vals if v is not None]
                else:
                    f_meta["distinct_values"] = None
            except Exception:
                pass

        fields_list.append(f_meta)

    summary_doc["fields"] = fields_list

    # Upsert into dataset_catalog
    await a_coll.update_one(
        {"company_id": str(company_id), "dataset": dataset_key},
        {"$set": summary_doc},
        upsert=True,
    )

    return summary_doc


async def rebuild_tenant_summaries(
    company_id: str,
    reader_db: Any = None,
    app_db: Any = None,
) -> List[Dict[str, Any]]:
    """Rebuilds dataset summaries across all 5 structured datasets for a tenant."""
    summaries = []
    for d_key in DATASET_CATALOG.keys():
        s = await build_dataset_summary(company_id, d_key, reader_db=reader_db, app_db=app_db)
        summaries.append(s)
    return summaries


async def get_tenant_summaries(
    company_id: str,
    app_db: Any = None,
) -> List[Dict[str, Any]]:
    """Retrieves cached dataset summaries from app database, building on demand if absent."""
    a_coll = (app_db["dataset_catalog"] if app_db is not None else dataset_catalog_collection)
    cursor = a_coll.find({"company_id": str(company_id)})
    docs = await cursor.to_list(20)

    if not docs or len(docs) < len(DATASET_CATALOG):
        # Build missing summaries
        docs = await rebuild_tenant_summaries(company_id, app_db=app_db)

    return docs


def format_catalog_summary_for_prompt(
    summaries: List[Dict[str, Any]],
    max_bytes: int = MAX_CATALOG_SUMMARY_BYTES,
) -> str:
    """
    Composes a natural language schema understanding text for LLM system prompt.
    Lists collections with data, row counts, queryable fields, min/max ranges,
    and distinct values. Marks datasets without data as 'no data uploaded'.
    Excludes personal non-queryable fields entirely.
    """
    lines = []
    for s in summaries:
        d_name = s.get("dataset", "")
        row_count = s.get("row_count", 0)

        if row_count == 0:
            lines.append(f"### Collection: `{d_name}` - no data uploaded (0 rows)")
            continue

        last_imp = s.get("last_imported_at") or "unknown"
        lines.append(f"### Collection: `{d_name}` ({row_count} rows, updated: {last_imp})")

        fields = s.get("fields", [])
        for f in fields:
            # Strictly exclude personal / non-queryable fields
            if not f.get("queryable", True):
                continue

            f_name = f.get("name")
            f_type = f.get("type", "string")
            f_desc = f.get("description", "")

            extra_parts = []
            if f.get("min") is not None and f.get("max") is not None:
                extra_parts.append(f"range: [{f['min']} to {f['max']}]")
            if f.get("distinct_values"):
                vals_str = ", ".join(f"'{v}'" for v in f["distinct_values"])
                extra_parts.append(f"valid values: [{vals_str}]")

            extra = f" ({'; '.join(extra_parts)})" if extra_parts else ""
            lines.append(f"  - `{f_name}` ({f_type}): {f_desc}{extra}")

    result = "\n".join(lines)
    # Enforce strict 15 KB cap
    encoded = result.encode("utf-8")
    if len(encoded) > max_bytes:
        result = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return result
