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


def _inspect_tree(node: Any, collection_fields: Set[str], in_group_accum: bool = False) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in FORBIDDEN_OPERATORS:
                raise SqlValidationFailedError(f"Forbidden operator '{k}' detected.")
            if k.startswith("$"):
                if k not in ALLOWED_OPERATORS:
                    raise SqlValidationFailedError(f"Operator '{k}' is not in allowed operator list.")
            else:
                if "company_id" in k:
                    raise SqlValidationFailedError("company_id is forbidden in query fields.")
                if k in ("email", "phone"):
                    raise SqlValidationFailedError(f"Field '{k}' is personal data and not queryable.")
            if k in ("$regex", "$regexMatch"):
                if not isinstance(v, str) or len(v) > MAX_REGEX_LEN:
                    raise SqlValidationFailedError("Regex pattern must be a string literal of at most 40 characters.")
            _inspect_tree(v, collection_fields, in_group_accum=(k == "$group"))
    elif isinstance(node, list):
        for item in node:
            _inspect_tree(item, collection_fields, in_group_accum)
    elif isinstance(node, str):
        if node.startswith("$$"):
            if node not in ("$$ROOT", "$$CURRENT"):
                raise SqlValidationFailedError(f"Disallowed variable '{node}'.")
        elif node.startswith("$"):
            f_ref = node[1:]
            if "company_id" in f_ref:
                raise SqlValidationFailedError("Referencing company_id is forbidden.")
            if f_ref in ("email", "phone") or f_ref.endswith(".email") or f_ref.endswith(".phone"):
                raise SqlValidationFailedError(f"Referencing personal field '{f_ref}' is forbidden.")


def validate_and_format_sql(
    query_spec: Any,
    allowed_collections: Optional[Set[str]] = None,
    catalog_fields: Optional[Dict[str, Set[str]]] = None,
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

    # 2. Inspect stages, depth, and operators
    fields = (catalog_fields or {}).get(collection, COLLECTION_CATALOG_FIELDS.get(collection, set()))
    for stage in pipeline:
        _check_depth(stage, depth=1)
        if not isinstance(stage, dict) or len(stage) != 1:
            raise SqlValidationFailedError("Each pipeline stage must be an object with exactly one key.")
        stage_name = next(iter(stage.keys()))
        if stage_name not in ALLOWED_STAGES:
            raise SqlValidationFailedError(f"Stage '{stage_name}' is not in allowed stages.")
        _inspect_tree(stage, fields)

    # 3. Final stage must be $project with _id: 0
    last_stage = pipeline[-1]
    if "$project" not in last_stage:
        raise SqlValidationFailedError("The last stage of the pipeline must be $project.")
    proj = last_stage["$project"]
    if not isinstance(proj, dict) or proj.get("_id") != 0:
        raise SqlValidationFailedError("The last $project stage must suppress _id with '_id': 0.")

    columns = [k for k in proj.keys() if k != "_id"]
    if not columns:
        raise SqlValidationFailedError("The final $project stage must define at least one scalar output column.")

    # 4. Enforce $limit exists and is capped at MAX_ROWS
    has_limit = False
    for stage in pipeline:
        if "$limit" in stage:
            has_limit = True
            if not isinstance(stage["$limit"], int) or stage["$limit"] > MAX_ROWS or stage["$limit"] <= 0:
                stage["$limit"] = MAX_ROWS

    if not has_limit:
        pipeline.insert(len(pipeline) - 1, {"$limit": MAX_ROWS})

    normalized = {"collection": collection, "pipeline": pipeline}
    return normalized, columns
