"""
Relationship Detection Engine (app/modules/file_connections/detection.py)
Candidate generation, normalization level selection, scoring, false positive guards,
and direction determination. Pure Python - strictly no motor, fastapi, or csv imports.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from app.modules.file_connections.scoring import (
    LOW_CARDINALITY_NAMES,
    calculate_file_hint,
    calculate_name_score,
    compute_confidence,
)
from app.modules.file_connections.validation import (
    check_type_compatibility,
    evaluate_right_key_uniqueness,
    normalize_key_value,
)


def evaluate_column_containment(
    left_vals: List[Any],
    right_vals: List[Any],
    col_type: str,
) -> Tuple[int, float, float, int, int]:
    """
    Selects lowest normalization level (0, 1, 2) that reaches containment >= 0.9.
    Returns: (best_level, containment, row_match_rate, left_distinct_count, right_distinct_count)
    """
    best_level = 0
    best_containment = 0.0
    best_match_rate = 0.0
    best_l_dist = 0
    best_r_dist = 0

    for level in (0, 1, 2):
        r_keys = set()
        for v in right_vals:
            nv = normalize_key_value(v, col_type, level=level)
            if nv is not None:
                r_keys.add(nv)

        l_keys = set()
        l_non_null_count = 0
        l_matched_rows = 0
        for v in left_vals:
            nv = normalize_key_value(v, col_type, level=level)
            if nv is not None:
                l_keys.add(nv)
                l_non_null_count += 1
                if nv in r_keys:
                    l_matched_rows += 1

        if not l_keys or not r_keys:
            continue

        matched_keys = len(l_keys & r_keys)
        containment = matched_keys / len(l_keys)
        row_match_rate = (l_matched_rows / l_non_null_count) if l_non_null_count else 0.0

        if level == 0 or containment > best_containment:
            best_level = level
            best_containment = containment
            best_match_rate = row_match_rate
            best_l_dist = len(l_keys)
            best_r_dist = len(r_keys)

        if containment >= 0.90:
            best_level = level
            best_containment = containment
            best_match_rate = row_match_rate
            best_l_dist = len(l_keys)
            best_r_dist = len(r_keys)
            break

    return best_level, round(best_containment, 4), round(best_match_rate, 4), best_l_dist, best_r_dist


def evaluate_candidate_pair(
    left_file: Dict[str, Any],
    right_file: Dict[str, Any],
    left_col: Dict[str, Any],
    right_col: Dict[str, Any],
    left_vals: List[Any],
    right_vals: List[Any],
    high_threshold: float = 0.85,
    min_containment: float = 0.90,
    suggest_threshold: float = 0.60,
) -> Optional[Dict[str, Any]]:
    col_type = left_col.get("type", "text")
    l_key = left_col["key"]
    r_key = right_col["key"]

    # Low-cardinality exclusions
    if l_key.lower() in LOW_CARDINALITY_NAMES or r_key.lower() in LOW_CARDINALITY_NAMES:
        return None
    if left_col.get("role") == "category" or right_col.get("role") == "category":
        return None

    norm_level, containment, row_match_rate, l_dist, r_dist = evaluate_column_containment(
        left_vals, right_vals, col_type
    )
    if r_dist < 3 or l_dist == 0:
        return None

    # Verify right uniqueness at chosen level
    is_unique, _, _ = evaluate_right_key_uniqueness(right_vals, col_type, level=norm_level)
    if not is_unique:
        return None

    name_score = calculate_name_score(l_key, r_key)
    file_hint = max(
        calculate_file_hint(l_key, right_file.get("slug", ""), right_file.get("name", "")),
        calculate_file_hint(r_key, left_file.get("slug", ""), left_file.get("name", "")),
    )

    # Numeric guard: unrelated counters must not connect
    if col_type == "number" and name_score < 0.4 and file_hint <= 0:
        return None

    l_rows = len(left_vals)
    uniqueness_quality = 1.0 if (is_unique and l_rows >= r_dist) else 0.5
    confidence = compute_confidence(containment, name_score, file_hint, uniqueness_quality)

    # Text guard: low name score and no hint cannot be high
    if col_type == "text" and name_score < 0.2 and file_hint <= 0:
        confidence = min(confidence, 0.79)

    if confidence < suggest_threshold:
        return None

    label = "high" if (confidence >= high_threshold and containment >= min_containment) else "medium"

    reason_codes = []
    if name_score >= 0.6:
        reason_codes.append("column_names_match")
    if file_hint > 0:
        reason_codes.append("file_name_match")
    if containment >= 0.9:
        reason_codes.append("containment_90_plus")
    if is_unique:
        reason_codes.append("right_key_unique")

    # Check one_to_one
    l_unique, _, _ = evaluate_right_key_uniqueness(left_vals, col_type, level=norm_level)
    cardinality = "one_to_one" if l_unique else "many_to_one"

    return {
        "left_file_id": str(left_file["_id"]),
        "left_file_name": left_file.get("name", ""),
        "left_key": l_key,
        "right_file_id": str(right_file["_id"]),
        "right_file_name": right_file.get("name", ""),
        "right_key": r_key,
        "cardinality": cardinality,
        "confidence": confidence,
        "confidence_label": label,
        "normalization_level": norm_level,
        "evidence": {
            "name_score": name_score,
            "file_hint": file_hint,
            "containment": containment,
            "row_match_rate": row_match_rate,
            "left_distinct": l_dist,
            "right_distinct": r_dist,
            "right_unique": is_unique,
            "reason_codes": reason_codes,
            "llm_review": None,
        },
    }
