"""
Connection Validation Helpers (app/modules/file_connections/validation.py)
Normalization, type compatibility, and match rate calculations.
Pure Python - strictly no motor, pymongo, fastapi, or csv imports.
"""

import re
from typing import Any, List, Optional, Set, Tuple


def normalize_key_value(val: Any, col_type: str, level: int = 0) -> Optional[Any]:
    """
    Normalize key value at level 0, 1, or 2:
    Level 0: trim, case-fold, numeric compare.
    Level 1: also remove punctuation, collapse spaces, drop honorifics, and normalize '&' vs 'and'.
    Level 2: also strip leading zeros in digit strings.
    """
    if val is None:
        return None
    if col_type == "number":
        try:
            f = float(val)
            return int(f) if f.is_integer() else f
        except (ValueError, TypeError):
            return None
    s = str(val).strip()
    if not s:
        return None
    s = s.casefold()

    if level >= 1:
        s = re.sub(r"^(?:dr|mr|mrs|ms|shri|smt)\.?\s+", "", s)
        s = s.replace("&", " and ")
        s = re.sub(r"['\"]", "", s)
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()

    if level >= 2:
        s = re.sub(r"\b0+([0-9]+)\b", r"\1", s)

    return s or None


def get_type_family(col_type: str) -> Optional[str]:
    """Return 'text' or 'number'; date or others return None (not allowed)."""
    t = (col_type or "text").lower()
    if t in ("number", "integer", "float"):
        return "number"
    if t in ("text", "string"):
        return "text"
    return None


def check_type_compatibility(left_type: str, right_type: str) -> bool:
    """Ensure left and right have same type family (text/text or number/number, no date)."""
    lf = get_type_family(left_type)
    rf = get_type_family(right_type)
    return lf is not None and lf == rf


def evaluate_right_key_uniqueness(
    raw_right_values: List[Any], col_type: str, level: int = 0
) -> Tuple[bool, Set[Any], List[Any]]:
    """Check if right key is unique among its non-null normalized values."""
    seen: Set[Any] = set()
    duplicates: List[Any] = []
    unique_keys: Set[Any] = set()

    for raw in raw_right_values:
        norm = normalize_key_value(raw, col_type, level=level)
        if norm is None:
            continue
        if norm in seen:
            if norm not in duplicates:
                duplicates.append(norm)
        else:
            seen.add(norm)
        unique_keys.add(norm)

    is_unique = len(duplicates) == 0
    return is_unique, unique_keys, duplicates[:5]


def calculate_match_metrics(
    raw_left_values: List[Any],
    unique_right_keys: Set[Any],
    col_type: str,
    level: int = 0,
) -> Tuple[float, int, int, List[Any]]:
    """Calculate match_rate, matched_count, distinct_left_count, and unmatched samples (max 5)."""
    distinct_left: Set[Any] = set()
    raw_by_norm: dict = {}

    for raw in raw_left_values:
        norm = normalize_key_value(raw, col_type, level=level)
        if norm is not None:
            distinct_left.add(norm)
            if norm not in raw_by_norm:
                raw_by_norm[norm] = raw

    if not distinct_left:
        return 0.0, 0, 0, []

    matched = 0
    unmatched_samples: List[Any] = []
    for norm in distinct_left:
        if norm in unique_right_keys:
            matched += 1
        elif len(unmatched_samples) < 5:
            unmatched_samples.append(raw_by_norm[norm])

    match_rate = matched / len(distinct_left)
    return round(match_rate, 4), matched, len(distinct_left), unmatched_samples
