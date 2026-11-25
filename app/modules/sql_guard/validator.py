"""
Pipeline Validator (app/modules/sql_guard/validator.py)
modules/sql_guard now validates MongoDB aggregation pipelines.
Enforces stage allow-lists, forbidden operator checks, field catalogs, and final project/limit stages.
"""

import json
from typing import Any, Dict, List, Optional, Set, Tuple
from app.core.exceptions import SqlValidationFailedError
from app.modules.sql_guard.rules import (
    ALLOWED_OPERATORS,
    ALLOWED_STAGES,
    COLLECTION_CATALOG_FIELDS,
    DEFAULT_ALLOWED_COLLECTIONS,
    FORBIDDEN_OPERATORS,
    MAX_BYTES,
    MAX_DEPTH,
    MAX_REGEX_LEN,
    MAX_ROWS,
    MAX_STAGES,
)


def _check_depth(node: Any, depth: int = 1) -> None:
    if depth > MAX_DEPTH:
        raise SqlValidationFailedError("Query specification exceeds maximum nesting depth.")
    if isinstance(node, dict):
        for v in node.values():
            _check_depth(v, depth + 1)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _check_depth(item, depth)


def _inspect_tree(
    node: Any,
    collection_fields: Set[str],
    hidden_fields: Optional[Set[str]] = None,
    in_group_accum: bool = False,
    stage_name: str = "",
    stage_produced_fields: Optional[Set[str]] = None,
) -> None:
    hidden = hidden_fields or set()
    produced = stage_produced_fields or set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in FORBIDDEN_OPERATORS:
                raise SqlValidationFailedError(f"Forbidden operator '{k}' detected.")
            if k.startswith("$"):
                if k not in ALLOWED_OPERATORS:
                    raise SqlValidationFailedError(f"Operator '{k}' is not in allowed operator list.")
            else:
                if "company_id" in k or "file_id" in k or "import_id" in k:
                    raise SqlValidationFailedError(f"Field '{k}' is forbidden in query fields.")
                if k in ("email", "phone") or k in hidden:
                    raise SqlValidationFailedError(f"Field '{k}' is personal/hidden data and not queryable.")
                if stage_name == "$match" and collection_fields and k not in collection_fields:
                    raise SqlValidationFailedError(f"Field '{k}' is not in allowed collection fields.")
                if "." in k and collection_fields:
                    if k not in collection_fields and k not in produced and k.split(".")[0] not in produced and k != "items.product_name":
                        raise SqlValidationFailedError(f"Field '{k}' is unknown or unrequested join field.")
            if k in ("$regex", "$regexMatch"):
                if not isinstance(v, str) or len(v) > MAX_REGEX_LEN:
                    raise SqlValidationFailedError("Regex pattern must be a string literal of at most 40 characters.")
            _inspect_tree(v, collection_fields, hidden_fields, in_group_accum=(k == "$group"), stage_name=stage_name, stage_produced_fields=produced)
    elif isinstance(node, list):
        for item in node:
            _inspect_tree(item, collection_fields, hidden_fields, in_group_accum, stage_name=stage_name, stage_produced_fields=produced)
    elif isinstance(node, str):
        if node.startswith("$$"):
            if node not in ("$$ROOT", "$$CURRENT"):
                raise SqlValidationFailedError(f"Disallowed variable '{node}'.")
        elif node.startswith("$"):
            f_ref = node[1:]
            if "company_id" in f_ref or "file_id" in f_ref or "import_id" in f_ref:
                raise SqlValidationFailedError("Referencing company_id/file_id is forbidden.")
            if f_ref in ("email", "phone") or f_ref.endswith(".email") or f_ref.endswith(".phone") or f_ref in hidden:
                raise SqlValidationFailedError(f"Referencing personal/hidden field '{f_ref}' is forbidden.")
            if stage_name == "$match" and collection_fields and f_ref not in collection_fields:
                raise SqlValidationFailedError(f"Referencing unknown field '{f_ref}'.")
            if "." in f_ref and collection_fields:
                if f_ref not in collection_fields and f_ref not in produced and f_ref.split(".")[0] not in produced and f_ref != "items.product_name":
                    raise SqlValidationFailedError(f"Referencing unknown or unrequested join field '{f_ref}'.")


def validate_and_format_sql(
    query_spec: Any,
    allowed_collections: Optional[Set[str]] = None,
    catalog_fields: Optional[Dict[str, Set[str]]] = None,
    hidden_fields: Optional[Set[str]] = None,
    connections: Optional[List[Dict[str, Any]]] = None,
    company_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Validate MongoDB query spec and return normalized spec with output columns."""
    if isinstance(query_spec, str):
        try:
            query_spec = json.loads(query_spec)
        except Exception:
            raise SqlValidationFailedError("Query specification must be valid JSON.")

    if not isinstance(query_spec, dict):
        raise SqlValidationFailedError("Query specification must be a dictionary.")

    # 1. Enforce payload size
    serialized = json.dumps(query_spec)
    if len(serialized.encode("utf-8")) > MAX_BYTES:
        raise SqlValidationFailedError("Query specification exceeds 8 KB limit.")

    collection = query_spec.get("collection")
    pipeline = query_spec.get("pipeline")
    allowed = allowed_collections or DEFAULT_ALLOWED_COLLECTIONS
    if not collection or collection not in allowed:
        raise SqlValidationFailedError(f"Collection '{collection}' is not in allowed list.")

    if not isinstance(pipeline, list) or not (1 <= len(pipeline) <= MAX_STAGES):
        raise SqlValidationFailedError(f"Pipeline must contain between 1 and {MAX_STAGES} stages.")

    # 2. Inspect joins if present
    joins = query_spec.get("joins")
    requested_joins: List[str] = []
    allowed_join_fields: Set[str] = set()
    if joins is not None:
        if not isinstance(joins, list):
            raise SqlValidationFailedError("'joins' must be a list of connection IDs.")
        from app.core.config import settings
        max_joins = getattr(settings, "QUERY_MAX_JOINS", 2)
        if len(joins) > max_joins:
            raise SqlValidationFailedError(f"Query specifies {len(joins)} joins, exceeding maximum of {max_joins}.")

        conn_map = {str(c.get("id")): c for c in (connections or [])}
        for j_id in joins:
            j_id_str = str(j_id)
            if j_id_str not in conn_map:
                raise SqlValidationFailedError(f"Connection '{j_id_str}' not found or not active.")
            c_doc = conn_map[j_id_str]
            if c_doc.get("status") != "active":
                raise SqlValidationFailedError(f"Connection '{j_id_str}' is not active.")
            if company_id and str(c_doc.get("company_id")) != str(company_id):
                raise SqlValidationFailedError(f"Connection '{j_id_str}' does not belong to tenant.")
            left_slug = c_doc.get("left_file_slug")
            if left_slug and left_slug != collection:
                raise SqlValidationFailedError(f"Connection '{j_id_str}' left file does not match query collection '{collection}'.")
            alias = c_doc.get("alias")
            requested_joins.append(j_id_str)
            raw_all = (catalog_fields or {}).get(collection, set())
            for f in raw_all:
                if f.startswith(f"{alias}."):
                    allowed_join_fields.add(f)

    # 3. Base fields vs joined fields
    raw_fields = set((catalog_fields or {}).get(collection, COLLECTION_CATALOG_FIELDS.get(collection, set())))
    base_fields = {f for f in raw_fields if "." not in f}
    if collection == "orders":
        base_fields.add("items.product_name")
    fields = base_fields | allowed_join_fields

    # 4. Inspect stages, depth, and operators
    stage_produced_fields: Set[str] = set()
    for stage in pipeline:
        _check_depth(stage, depth=1)
        if not isinstance(stage, dict) or len(stage) != 1:
            raise SqlValidationFailedError("Each pipeline stage must be an object with exactly one key.")
        stage_name = next(iter(stage.keys()))
        if stage_name not in ALLOWED_STAGES:
            raise SqlValidationFailedError(f"Stage '{stage_name}' is not in allowed stages.")
        _inspect_tree(stage, fields, hidden_fields, stage_name=stage_name, stage_produced_fields=stage_produced_fields)
        if stage_name in ("$group", "$project", "$addFields", "$set") and isinstance(stage[stage_name], dict):
            stage_produced_fields.update(stage[stage_name].keys())
        elif stage_name == "$count" and isinstance(stage[stage_name], str):
            stage_produced_fields.add(stage[stage_name])

    # 5. Final stage must be $project with _id: 0
    last_stage = pipeline[-1]
    if "$project" not in last_stage:
        raise SqlValidationFailedError("The last stage of the pipeline must be $project.")
    proj = last_stage["$project"]
    if not isinstance(proj, dict) or proj.get("_id") != 0:
        raise SqlValidationFailedError("The last $project stage must suppress _id with '_id': 0.")

    hidden = hidden_fields or set()
    allowed_proj_sources = (fields | stage_produced_fields | {"_id"}) if fields else set()
    for col_key, col_val in proj.items():
        if col_key == "_id":
            continue
        if "company_id" in col_key or "file_id" in col_key or "import_id" in col_key or col_key in ("email", "phone") or col_key in hidden:
            raise SqlValidationFailedError(f"Field '{col_key}' is forbidden or hidden.")
        if col_val == 1 and allowed_proj_sources and col_key not in allowed_proj_sources:
            raise SqlValidationFailedError(f"Field '{col_key}' is unknown or hidden.")
        if isinstance(col_val, str) and col_val.startswith("$") and not col_val.startswith("$$"):
            ref = col_val[1:]
            if "company_id" in ref or "file_id" in ref or "import_id" in ref or ref in ("email", "phone") or ref in hidden:
                raise SqlValidationFailedError(f"Referencing field '{ref}' is forbidden or hidden.")
            if allowed_proj_sources and ref not in allowed_proj_sources:
                raise SqlValidationFailedError(f"Referencing unknown or hidden field '{ref}'.")

    columns = [k for k in proj.keys() if k != "_id"]
    if not columns:
        raise SqlValidationFailedError("The final $project stage must define at least one scalar output column.")

    # 6. Enforce $limit exists and is capped at MAX_ROWS
    has_limit = False
    for stage in pipeline:
        if "$limit" in stage:
            has_limit = True
            if not isinstance(stage["$limit"], int) or stage["$limit"] > MAX_ROWS or stage["$limit"] <= 0:
                stage["$limit"] = MAX_ROWS

    if not has_limit:
        pipeline.insert(len(pipeline) - 1, {"$limit": MAX_ROWS})

    normalized = {"collection": collection, "pipeline": pipeline}
    if requested_joins:
        normalized["joins"] = requested_joins
    return normalized, columns
    return normalized, columns
