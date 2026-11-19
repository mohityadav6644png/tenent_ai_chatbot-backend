"""
Plan Translator (app/modules/chat/plan_translator.py)
Deterministic translator from QueryPlan to safe MongoDB aggregation pipelines.
Performs type-aware validation, value coercion, regex escaping, and pipeline construction.
"""

from datetime import datetime
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.exceptions import SqlValidationFailedError
from app.modules.chat.query_plan import QueryPlan, QueryPlanFilter
from app.modules.sql_guard.rules import (
    COLLECTION_CATALOG_FIELDS,
    DEFAULT_ALLOWED_COLLECTIONS,
    MAX_REGEX_LEN,
    MAX_ROWS,
)


NUMERIC_FIELDS = {
    "total", "price", "amount", "quantity_on_hand", "reorder_threshold",
    "qty", "items.qty", "items.price",
}
DATE_FIELDS = {"order_date", "created_at", "invoice_date"}


def _is_date_field(field: str, field_type: Optional[str]) -> bool:
    if field_type == "date":
        return True
    return field in DATE_FIELDS or field.endswith("_date") or field.endswith("_at")


def _is_numeric_field(field: str, field_type: Optional[str]) -> bool:
    if field_type in ("number", "integer", "float"):
        return True
    return field in NUMERIC_FIELDS


def _coerce_date(value: Any) -> Dict[str, str]:
    if isinstance(value, dict) and "$date" in value:
        return value
    if isinstance(value, datetime):
        return {"$date": value.isoformat()}
    if isinstance(value, str):
        cleaned = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
            return {"$date": dt.isoformat()}
        except Exception:
            # Fallback if just YYYY-MM-DD
            try:
                dt = datetime.strptime(cleaned[:10], "%Y-%m-%d")
                return {"$date": dt.isoformat()}
            except Exception:
                raise SqlValidationFailedError(f"Cannot parse date literal '{value}'.")
    raise SqlValidationFailedError(f"Invalid date value '{value}'.")


def _coerce_numeric(value: Any) -> float | int:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        try:
            f = float(cleaned)
            return int(f) if f.is_integer() else f
        except ValueError:
            raise SqlValidationFailedError(f"Value '{value}' is not a valid number.")
    raise SqlValidationFailedError(f"Invalid numeric value '{value}'.")


def _coerce_filter_value(val_str: str, field: str, op: str, field_type: Optional[str]) -> Any:
    """Parse JSON string and coerce to type expected by catalog."""
    try:
        raw = json.loads(val_str)
    except Exception:
        raw = val_str.strip()

    is_date = _is_date_field(field, field_type)
    is_num = _is_numeric_field(field, field_type)

    if op in ("gt", "gte", "lt", "lte", "between"):
        if not (is_date or is_num):
            raise SqlValidationFailedError(
                f"Operator '{op}' is only allowed on numeric or date fields, got '{field}'."
            )

    if op == "contains":
        if is_date or is_num:
            raise SqlValidationFailedError(
                f"Operator 'contains' is only allowed on string fields, got '{field}'."
            )
        # Escape literal regex and cap length
        clean = re.escape(str(raw)[:MAX_REGEX_LEN])
        return {"$regex": clean}

    if op == "between":
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise SqlValidationFailedError("Operator 'between' requires a 2-element array [min, max].")
        low, high = raw[0], raw[1]
        if is_date:
            return {"$gte": _coerce_date(low), "$lte": _coerce_date(high)}
        elif is_num:
            return {"$gte": _coerce_numeric(low), "$lte": _coerce_numeric(high)}
        return {"$gte": low, "$lte": high}

    if op == "in":
        if not isinstance(raw, list):
            raw = [raw]
        if is_date:
            return {"$in": [_coerce_date(x) for x in raw]}
        elif is_num:
            return {"$in": [_coerce_numeric(x) for x in raw]}
        return {"$in": [str(x) for x in raw]}

    # Scalar comparisons: eq, ne, gt, gte, lt, lte
    coerced = raw
    if is_date:
        coerced = _coerce_date(raw)
    elif is_num:
        coerced = _coerce_numeric(raw)
    elif isinstance(raw, str):
        coerced = raw

    if op == "eq":
        return coerced
    return {f"${op}": coerced}


def translate_plan_to_query(
    plan: QueryPlan,
    catalog: Optional[Dict[str, Any]] = None,
    max_limit: int = 50,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Deterministically translates a validated QueryPlan into a MongoDB aggregation query spec.
    Returns (query_spec, column_names).
    """
    if plan.decision != "answer":
        raise SqlValidationFailedError(f"Cannot translate query plan with decision '{plan.decision}'.")

    collection = plan.collection
    if not collection:
        raise SqlValidationFailedError("Query plan must specify a target collection.")

    catalog_data = catalog or {}
    allowed_cols = set(catalog_data.get("allowed_collections", [])) | DEFAULT_ALLOWED_COLLECTIONS
    if collection not in allowed_cols:
        raise SqlValidationFailedError(f"Collection '{collection}' is not in allowed collections.")

    # Gather field metadata
    col_catalog = catalog_data.get("collections", {}).get(collection, {})
    col_fields = col_catalog.get("fields", {})
    known_fields = set(col_fields.keys()) | set(COLLECTION_CATALOG_FIELDS.get(collection, set()))

    pipeline: List[Dict[str, Any]] = []

    # 1. $match stage from filters
    if plan.filters:
        if len(plan.filters) > 8:
            raise SqlValidationFailedError("Plan exceeds maximum of 8 filters.")
        match_doc: Dict[str, Any] = {}
        for f in plan.filters:
            field_name = f.field
            if field_name not in known_fields and not any(field_name.startswith(f"{k}.") for k in known_fields):
                raise SqlValidationFailedError(f"Field '{field_name}' is not in catalog for '{collection}'.")
            if "company_id" in field_name:
                raise SqlValidationFailedError("Referencing company_id in filters is forbidden.")

            field_type = col_fields.get(field_name, {}).get("type")
            cond = _coerce_filter_value(f.value, field_name, f.op, field_type)
            if field_name in match_doc:
                # Merge if multiple conditions on same field
                if isinstance(match_doc[field_name], dict) and isinstance(cond, dict):
                    match_doc[field_name].update(cond)
                else:
                    match_doc[field_name] = cond
            else:
                match_doc[field_name] = cond
        if match_doc:
            pipeline.append({"$match": match_doc})

    # 2. Check if grouping / aggregation is requested
    has_group = bool(plan.group_by or plan.metrics or plan.date_bucket)

    output_columns: List[str] = []

    if has_group:
        if len(plan.group_by) > 2:
            raise SqlValidationFailedError("Plan exceeds maximum of 2 group_by fields.")
        if len(plan.metrics) > 4:
            raise SqlValidationFailedError("Plan exceeds maximum of 4 metrics.")

        # Validate group_by fields
        for gf in plan.group_by:
            if gf not in known_fields:
                raise SqlValidationFailedError(f"group_by field '{gf}' is not in catalog for '{collection}'.")

        # Validate date_bucket
        if plan.date_bucket:
            db_field = plan.date_bucket.field
            if db_field not in known_fields:
                raise SqlValidationFailedError(f"date_bucket field '{db_field}' is not in catalog for '{collection}'.")
            db_type = col_fields.get(db_field, {}).get("type")
            if not _is_date_field(db_field, db_type):
                raise SqlValidationFailedError(f"date_bucket field '{db_field}' must be a date field.")

        # Build group _id
        group_id: Any = None
        if plan.date_bucket:
            trunc_expr = {"$dateTrunc": {"date": f"${plan.date_bucket.field}", "unit": plan.date_bucket.unit}}
            if not plan.group_by:
                group_id = trunc_expr
            else:
                group_id = {gf: f"${gf}" for gf in plan.group_by}
                date_key = plan.date_bucket.field if plan.date_bucket.field not in group_id else f"{plan.date_bucket.field}_{plan.date_bucket.unit}"
                group_id[date_key] = trunc_expr
        elif plan.group_by:
            if len(plan.group_by) == 1:
                group_id = f"${plan.group_by[0]}"
            else:
                group_id = {gf: f"${gf}" for gf in plan.group_by}
        else:
            group_id = None

        group_stage: Dict[str, Any] = {"_id": group_id}

        # Build metric accumulators
        for m in plan.metrics:
            as_name = m.as_name
            if m.fn == "count":
                group_stage[as_name] = {"$sum": 1}
            else:
                if not m.field or m.field not in known_fields:
                    raise SqlValidationFailedError(f"Metric field '{m.field}' is not in catalog for '{collection}'.")
                f_type = col_fields.get(m.field, {}).get("type")
                if not _is_numeric_field(m.field, f_type):
                    raise SqlValidationFailedError(f"Metric function '{m.fn}' requires a numeric field, got '{m.field}'.")
                group_stage[as_name] = {f"${m.fn}": f"${m.field}"}

        pipeline.append({"$group": group_stage})

        # Final projection to flatten _id and output scalar columns
        proj_stage: Dict[str, Any] = {"_id": 0}
        if plan.date_bucket:
            if not plan.group_by:
                bucket_name = plan.date_bucket.field
                proj_stage[bucket_name] = "$_id"
                output_columns.append(bucket_name)
            else:
                for gf in plan.group_by:
                    proj_stage[gf] = f"$_id.{gf}"
                    output_columns.append(gf)
                date_key = plan.date_bucket.field if plan.date_bucket.field not in plan.group_by else f"{plan.date_bucket.field}_{plan.date_bucket.unit}"
                proj_stage[date_key] = f"$_id.{date_key}"
                output_columns.append(date_key)
        elif plan.group_by:
            if len(plan.group_by) == 1:
                col_name = plan.group_by[0]
                proj_stage[col_name] = "$_id"
                output_columns.append(col_name)
            else:
                for gf in plan.group_by:
                    proj_stage[gf] = f"$_id.{gf}"
                    output_columns.append(gf)

        for m in plan.metrics:
            proj_stage[m.as_name] = f"${m.as_name}"
            output_columns.append(m.as_name)

    else:
        # Flat row listing
        selected = plan.select if plan.select else [f for f in known_fields if f not in ("_id", "id", "company_id")]
        if len(selected) > 10:
            selected = selected[:10]

        proj_stage = {"_id": 0}
        for sel in selected:
            if sel not in known_fields and not any(sel.startswith(f"{k}.") for k in known_fields):
                raise SqlValidationFailedError(f"Selected field '{sel}' not in catalog for '{collection}'.")
            proj_stage[sel] = 1
            output_columns.append(sel)

    # 3. $sort stage
    if plan.sort:
        if len(plan.sort) > 2:
            raise SqlValidationFailedError("Plan exceeds maximum of 2 sort fields.")
        sort_doc: Dict[str, int] = {}
        for s in plan.sort:
            direction = 1 if s.dir == "asc" else -1
            sort_field = s.field
            if has_group:
                # If grouping, sort field might be group _id or metric alias
                if sort_field in [m.as_name for m in plan.metrics]:
                    sort_doc[sort_field] = direction
                elif len(plan.group_by) == 1 and sort_field == plan.group_by[0]:
                    sort_doc["_id"] = direction
                elif isinstance(group_id, dict) and sort_field in group_id:
                    sort_doc[f"_id.{sort_field}"] = direction
                else:
                    sort_doc[sort_field] = direction
            else:
                if sort_field not in known_fields:
                    raise SqlValidationFailedError(f"Sort field '{sort_field}' not in catalog for '{collection}'.")
                sort_doc[sort_field] = direction
        if sort_doc:
            pipeline.append({"$sort": sort_doc})

    # 4. $limit stage (capped at max_limit and MAX_ROWS)
    effective_limit = min(plan.limit or 20, max_limit, MAX_ROWS)
    if effective_limit <= 0:
        effective_limit = 20
    pipeline.append({"$limit": effective_limit})

    # 5. Final $project stage
    pipeline.append({"$project": proj_stage})

    query_spec = {"collection": collection, "pipeline": pipeline}
    return query_spec, output_columns
