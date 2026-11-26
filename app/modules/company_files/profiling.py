"""
Company Files Column Profiling and Role Detection (app/modules/company_files/profiling.py)
Single-pass streaming statistical profiling and semantic role assignment.
Pure Python - strictly no motor, pymongo, fastapi, or csv imports.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

ID_NAME_TOKENS = {"id", "no", "number", "code", "sku", "key", "ref", "uuid"}
NAME_LIKE_TOKENS = {
    "name", "title", "label", "doctor", "student", "customer",
    "product", "salesperson", "employee", "patient", "teacher"
}


def is_id_like_name(key: str, label: str) -> bool:
    tokens = set(re.split(r"[_\s]+", f"{key} {label}".lower()))
    return bool(tokens & ID_NAME_TOKENS)


def is_name_like(key: str, label: str) -> bool:
    tokens = set(re.split(r"[_\s]+", f"{key} {label}".lower()))
    return bool(tokens & NAME_LIKE_TOKENS)


def is_key_like(key: str, label: str, role: str) -> bool:
    """Returns True if the column is key-like (identifier, name-like, or code)."""
    if role in ("identifier", "foreign_key"):
        return True
    return is_id_like_name(key, label) or is_name_like(key, label)


def profile_column(
    key: str,
    label: str,
    inferred_type: str,
    values: List[str],
    is_personal: bool = False,
    category_max_distinct: int = 50,
) -> Tuple[Dict[str, Any], str, float]:
    """
    Profile a column and assign its semantic role and confidence.
    Returns: (profile_dict, role, role_confidence)
    """
    total = len(values)
    if total == 0:
        return (
            {"distinct_count": 0, "null_ratio": 1.0, "unique_ratio": 0.0, "avg_length": 0.0},
            "text",
            0.5,
        )

    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    null_count = total - len(non_null)
    null_ratio = round(null_count / total, 4)
    distinct_vals = set(str(v).strip() for v in non_null)
    distinct_count = len(distinct_vals)
    unique_ratio = round((distinct_count / len(non_null)), 4) if non_null else 0.0
    avg_length = round(sum(len(str(v).strip()) for v in non_null) / len(non_null), 2) if non_null else 0.0

    profile = {
        "distinct_count": distinct_count,
        "null_ratio": null_ratio,
        "unique_ratio": unique_ratio,
        "avg_length": avg_length,
    }

    if is_personal:
        return (profile, "personal", 0.95)

    if inferred_type == "date":
        return (profile, "date", 0.95)

    id_name = is_id_like_name(key, label)
    name_like = is_name_like(key, label)

    # 1. Identifier
    if unique_ratio >= 0.95 and null_ratio <= 0.05:
        if id_name or name_like or (inferred_type == "text" and avg_length < 40):
            confidence = 0.95 if id_name else 0.85
            return (profile, "identifier", confidence)

    # 2. Category
    if inferred_type in ("text", "boolean") and distinct_count <= category_max_distinct and unique_ratio <= 0.2:
        return (profile, "category", 0.90)

    # 3. Free Text
    if inferred_type == "text" and avg_length > 40:
        return (profile, "text", 0.85)

    # 4. Measure
    if inferred_type == "number":
        if not (unique_ratio >= 0.95 and id_name):
            return (profile, "measure", 0.90)
        else:
            return (profile, "identifier", 0.85)

    return (profile, "text", 0.70)
