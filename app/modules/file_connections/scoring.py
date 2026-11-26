"""
Relationship Detection Scoring and Evidence (app/modules/file_connections/scoring.py)
Token stemming, synonym matching, evidence metrics, and confidence formula.
Pure Python - strictly no motor, pymongo, fastapi, or csv imports.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

SYNONYMS: Dict[str, str] = {
    "cust": "customer",
    "customer": "customer",
    "customers": "customer",
    "emp": "employee",
    "employee": "employee",
    "employees": "employee",
    "prod": "product",
    "product": "product",
    "products": "product",
    "doc": "doctor",
    "doctor": "doctor",
    "doctors": "doctor",
    "std": "student",
    "student": "student",
    "students": "student",
    "dept": "department",
    "salesperson": "salesperson",
    "sales_team": "salesperson",
    "sales_rep": "salesperson",
    "rep": "salesperson",
}

GENERIC_TOKENS: Set[str] = {"id", "no", "number", "code", "name", "key", "ref", "uid", "uuid"}
LOW_CARDINALITY_NAMES: Set[str] = {
    "status", "region", "category", "gender", "state", "type", "flag",
    "section", "fee_status", "result", "grade", "priority", "mode"
}


def stem_token(token: str) -> str:
    t = token.lower().strip()
    if t.endswith("ies") and len(t) > 4:
        t = t[:-3] + "y"
    elif t.endswith("es") and len(t) > 4:
        t = t[:-2]
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        t = t[:-1]
    return SYNONYMS.get(t, t)


def tokenize_name(name: str) -> List[str]:
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", name)
    tokens = [stem_token(t) for t in clean.split() if t]
    return tokens


def calculate_name_score(left_key: str, right_key: str) -> float:
    l_tokens = tokenize_name(left_key)
    r_tokens = tokenize_name(right_key)
    if not l_tokens or not r_tokens:
        return 0.0

    if l_tokens == r_tokens:
        return 1.0

    l_set = set(l_tokens)
    r_set = set(r_tokens)
    common = l_set & r_set
    if not common:
        return 0.0

    def token_weight(t: str) -> float:
        return 0.4 if t in GENERIC_TOKENS else 1.0

    common_weight = sum(token_weight(t) for t in common)
    total_weight = sum(token_weight(t) for t in (l_set | r_set))
    return round(common_weight / max(total_weight, 0.01), 4)


def calculate_file_hint(col_key: str, other_file_slug: str, other_file_name: str) -> float:
    col_tokens = set(tokenize_name(col_key))
    slug_tokens = set(tokenize_name(other_file_slug))
    name_tokens = set(tokenize_name(other_file_name))
    file_tokens = (slug_tokens | name_tokens) - GENERIC_TOKENS
    if not col_tokens or not file_tokens:
        return 0.0

    common = col_tokens & file_tokens
    if common:
        return 1.0
    return 0.0


def compute_confidence(
    containment: float,
    name_score: float,
    file_hint: float,
    uniqueness_quality: float,
) -> float:
    score = (
        0.45 * containment
        + 0.30 * name_score
        + 0.15 * file_hint
        + 0.10 * uniqueness_quality
    )
    return round(min(max(score, 0.0), 1.0), 4)
