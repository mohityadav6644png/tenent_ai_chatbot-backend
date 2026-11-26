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


def _resolve_field(name: Optional[str], known_fields: Set[str]) -> str:
    """Resolve field name against known_fields, handling exact match or common suffix/prefix variants."""
    if not name:
        return ""
    clean = str(name).strip().lstrip("$")
    if clean in known_fields:
        return clean

    # Check case-insensitive exact match
    for f in known_fields:
        if f.lower() == clean.lower():
            return f

    # Check joined fields with alias prefix: e.g. 'fee_status' -> '<alias>.fee_status'
    for f in known_fields:
        if f.lower().endswith(f".{clean.lower()}"):
            return f

    # Suffix/prefix variants: e.g. 'experience' -> 'experience_years', 'fee' -> 'fee', 'price' -> 'price_per_unit'
    candidates = [
        f for f in known_fields
        if f == f"{clean}_years"
        or f == f"{clean}_inr"
        or f == f"{clean}_date"
        or f == f"{clean}_id"
        or f.startswith(f"{clean}_")
        or f.endswith(f"_{clean}")
        or clean.startswith(f"{f}_")
        or clean.endswith(f"_{f}")
    ]
    if len(candidates) == 1:
        return candidates[0]

    return clean


def _coerce_date(value: Any) -> Dict[str, str]:
    if isinstance(value, dict) and "$date" in value:
        return value
    if isinstance(value, datetime):
        return {"$date": value.isoformat()}
    if isinstance(value, (int, float)):
        val_str = str(int(value))
        if re.match(r"^\d{4}$", val_str):
            return {"$date": f"{val_str}-01-01T00:00:00+00:00"}
    if isinstance(value, str):
        cleaned = value.strip().replace("Z", "+00:00")
        if re.match(r"^\d{4}$", cleaned):
            return {"$date": f"{cleaned}-01-01T00:00:00+00:00"}
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

    if is_date:
        # Check if raw value represents a 4-digit year like "2026" or 2026
        raw_str = str(raw).strip().strip('"\'')
        if re.match(r"^\d{4}$", raw_str):
            yr = int(raw_str)
            start_date = {"$date": f"{yr:04d}-01-01T00:00:00+00:00"}
            end_date = {"$date": f"{yr:04d}-12-31T23:59:59+00:00"}
            if op in ("eq", "contains"):
                return {"$gte": start_date, "$lte": end_date}
            elif op in ("gt", "gte"):
                return {"$gte": start_date}
            elif op in ("lt", "lte"):
                return {"$lte": end_date}

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
            low_str = str(low).strip().strip('"\'')
            high_str = str(high).strip().strip('"\'')
            if re.match(r"^\d{4}$", low_str):
                low = f"{low_str}-01-01T00:00:00+00:00"
            if re.match(r"^\d{4}$", high_str):
                high = f"{high_str}-12-31T23:59:59+00:00"
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

    # Resolve collection against Company Files first (handling case and prefixes like citycare_)
    req_col = collection.strip().lower()
    for f in catalog_data.get("files", []):
        f_slug = f.get("slug", "").strip().lower()
        f_name = f.get("name", "").strip().lower()
        if req_col in (f_slug, f_name) or req_col.endswith(f_slug) or f_slug.endswith(req_col):
            collection = f.get("slug")
            break

    allowed_cols = set(catalog_data.get("allowed_collections", [])) | DEFAULT_ALLOWED_COLLECTIONS
    if collection not in allowed_cols:
        raise SqlValidationFailedError(f"Collection '{collection}' is not in allowed collections.")

    # Gather field metadata
    col_catalog = catalog_data.get("collections", {}).get(collection, {})
    col_fields = dict(col_catalog.get("fields", {}))
    known_fields = set(col_fields.keys()) | set(COLLECTION_CATALOG_FIELDS.get(collection, set()))

    # Support admin-defined Company Files
    if not known_fields:
        for f in catalog_data.get("files", []):
            if f.get("slug") == collection or f.get("name") == collection:
                for c in f.get("columns", []):
                    k = c.get("key")
                    if not k or c.get("hidden"):
                        continue
                    c_type = c.get("type", "text")
                    mapped_type = "number" if c_type in ("number", "integer", "float") else "date" if c_type == "date" else "string"
                    col_fields[k] = {"type": mapped_type, "desc": c.get("meaning") or c.get("label", k)}
                    known_fields.add(k)
                break
        if not known_fields and collection in catalog_data.get("file_fields", {}):
            for k in catalog_data["file_fields"][collection]:
                known_fields.add(k)
                col_fields[k] = {"type": "string"}

    pipeline: List[Dict[str, Any]] = []

    # 1. $match stage from filters
    if plan.filters:
        if len(plan.filters) > 8:
            raise SqlValidationFailedError("Plan exceeds maximum of 8 filters.")
        match_doc: Dict[str, Any] = {}
        expr_conditions: List[Dict[str, Any]] = []
        for f in plan.filters:
            field_name = _resolve_field(f.field, known_fields)
            if field_name not in known_fields and not any(field_name.startswith(f"{k}.") for k in known_fields):
                raise SqlValidationFailedError(f"Field '{field_name}' is not in catalog for '{collection}'.")
            if "company_id" in field_name:
                raise SqlValidationFailedError("Referencing company_id in filters is forbidden.")

            field_type = col_fields.get(field_name, {}).get("type")

            # Check if f.value is a field reference for field-to-field comparison: e.g. "$reorder_level"
            try:
                parsed_val = json.loads(f.value)
            except Exception:
                parsed_val = f.value.strip()

            target_cand = _resolve_field(str(parsed_val).strip().lstrip("$"), known_fields)
            if target_cand in known_fields and target_cand != field_name and not isinstance(parsed_val, (int, float)):
                op_map = {
                    "eq": "$eq", "ne": "$ne", "gt": "$gt", "gte": "$gte",
                    "lt": "$lt", "lte": "$lte",
                }
                mongo_op = op_map.get(f.op, "$eq")
                expr_conditions.append({mongo_op: [f"${field_name}", f"${target_cand}"]})
                continue

            cond = _coerce_filter_value(f.value, field_name, f.op, field_type)
            if field_name in match_doc:
                # Merge if multiple conditions on same field
                if isinstance(match_doc[field_name], dict) and isinstance(cond, dict):
                    match_doc[field_name].update(cond)
                else:
                    match_doc[field_name] = cond
            else:
                match_doc[field_name] = cond

        if expr_conditions:
            if len(expr_conditions) == 1:
                match_doc["$expr"] = expr_conditions[0]
            else:
                match_doc["$expr"] = {"$and": expr_conditions}

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
        for idx, gf in enumerate(plan.group_by):
            res_gf = _resolve_field(gf, known_fields)
            if res_gf not in known_fields:
                raise SqlValidationFailedError(f"group_by field '{gf}' is not in catalog for '{collection}'.")
            plan.group_by[idx] = res_gf

        # Validate date_bucket
        if plan.date_bucket:
            db_field = _resolve_field(plan.date_bucket.field, known_fields)
            if db_field not in known_fields:
                raise SqlValidationFailedError(f"date_bucket field '{db_field}' is not in catalog for '{collection}'.")
            db_type = col_fields.get(db_field, {}).get("type")
            if not _is_date_field(db_field, db_type):
                raise SqlValidationFailedError(f"date_bucket field '{db_field}' must be a date field.")
            plan.date_bucket.field = db_field

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
                raw_m_field = (m.field or "").strip()
                # Check for multiplication e.g. "stock_units * price_per_unit"
                if "*" in raw_m_field:
                    parts = [p.strip().lstrip("$") for p in raw_m_field.split("*")]
                    if len(parts) == 2:
                        f1 = _resolve_field(parts[0], known_fields)
                        f2 = _resolve_field(parts[1], known_fields)
                        if f1 in known_fields and f2 in known_fields:
                            t1 = col_fields.get(f1, {}).get("type")
                            t2 = col_fields.get(f2, {}).get("type")
                            if _is_numeric_field(f1, t1) and _is_numeric_field(f2, t2):
                                group_stage[as_name] = {f"${m.fn}": {"$multiply": [f"${f1}", f"${f2}"]}}
                                continue

                res_m_field = _resolve_field(raw_m_field, known_fields)
                if not res_m_field or res_m_field not in known_fields:
                    raise SqlValidationFailedError(f"Metric field '{m.field}' is not in catalog for '{collection}'.")
                f_type = col_fields.get(res_m_field, {}).get("type")
                if not _is_numeric_field(res_m_field, f_type):
                    raise SqlValidationFailedError(f"Metric function '{m.fn}' requires a numeric field, got '{m.field}'.")
                group_stage[as_name] = {f"${m.fn}": f"${res_m_field}"}

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
        raw_select = plan.select if plan.select else [f for f in known_fields if f not in ("_id", "id", "company_id")]
        selected = []
        for sel in raw_select:
            res_sel = _resolve_field(sel, known_fields)
            if res_sel not in known_fields and not any(res_sel.startswith(f"{k}.") for k in known_fields):
                raise SqlValidationFailedError(f"Selected field '{sel}' not in catalog for '{collection}'.")
            selected.append(res_sel)

        if len(selected) > 10:
            selected = selected[:10]

        proj_stage = {"_id": 0}
        for sel in selected:
            proj_stage[sel] = 1
            output_columns.append(sel)

    # 3. $sort stage
    if plan.sort:
        if len(plan.sort) > 2:
            raise SqlValidationFailedError("Plan exceeds maximum of 2 sort fields.")
        sort_doc: Dict[str, int] = {}
        for s in plan.sort:
            direction = 1 if s.dir == "asc" else -1
            raw_sort_field = s.field
            res_sort_field = _resolve_field(raw_sort_field, known_fields) if raw_sort_field not in [m.as_name for m in plan.metrics] else raw_sort_field
            sort_field = res_sort_field
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
                    raise SqlValidationFailedError(f"Sort field '{raw_sort_field}' not in catalog for '{collection}'.")
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
