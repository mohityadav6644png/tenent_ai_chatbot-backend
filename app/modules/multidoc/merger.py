"""
Multi-document Record Merger (app/modules/multidoc/merger.py)
Deterministic merging, outer join correlation, column collision prefixing, and row capping.
Pure Python - no motor, redis, google-genai, or fastapi imports.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from app.modules.multidoc.join_detector import SYSTEM_EXCLUDED_KEYS, normalize_field_name


def _clean_val(v: Any) -> Any:
    return "" if v is None else v


def merge_document_records(
    doc_records: Dict[str, List[Dict[str, Any]]],
    doc_names: Dict[str, str],
    join_keys: List[str],
    max_merged_rows: int = 50,
    hidden_fields_by_doc: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[List[Dict[str, str]], List[List[Any]], bool]:
    """
    Deterministic outer-join merge across multiple documents.
    Resolves column collisions by prefixing with doc label.
    Strips hidden and system fields, capping total output rows.
    """
    if not doc_records:
        return [], [], False

    norm_join_keys = [normalize_field_name(k) for k in join_keys]
    hidden_by_doc = hidden_fields_by_doc or {}

    # Detect column collisions (same field in multiple docs not in join_keys)
    field_doc_counts: Dict[str, int] = {}
    for doc_id, records in doc_records.items():
        doc_hidden = {normalize_field_name(h) for h in hidden_by_doc.get(doc_id, set())}
        seen_in_doc: Set[str] = set()
        for r in records:
            for k in r.keys():
                nk = normalize_field_name(k)
                if nk not in SYSTEM_EXCLUDED_KEYS and nk not in doc_hidden and nk not in norm_join_keys:
                    seen_in_doc.add(nk)
        for nk in seen_in_doc:
            field_doc_counts[nk] = field_doc_counts.get(nk, 0) + 1

    merged_map: Dict[str, Dict[str, Any]] = {}
    row_order: List[str] = []
    unkeyed_idx = 0

    for doc_id, records in doc_records.items():
        doc_prefix = normalize_field_name(doc_names.get(doc_id, doc_id))
        doc_hidden = {normalize_field_name(h) for h in hidden_by_doc.get(doc_id, set())}

        for r in records:
            # Build match key
            key_parts = []
            has_all_keys = bool(norm_join_keys)
            for jk in norm_join_keys:
                val = next((v for k, v in r.items() if normalize_field_name(k) == jk), None)
                if val is not None and str(val).strip():
                    key_parts.append(str(val).strip().lower())
                else:
                    has_all_keys = False
                    break

            if has_all_keys:
                match_id = "::".join(key_parts)
            else:
                unkeyed_idx += 1
                match_id = f"__unmatched_{doc_id}_{unkeyed_idx}"

            if match_id not in merged_map:
                merged_map[match_id] = {}
                row_order.append(match_id)

            target_row = merged_map[match_id]

            for k, v in r.items():
                nk = normalize_field_name(k)
                if nk in SYSTEM_EXCLUDED_KEYS or nk in doc_hidden:
                    continue

                if nk in norm_join_keys:
                    if nk not in target_row or not target_row[nk]:
                        target_row[nk] = _clean_val(v)
                else:
                    # Prefix if colliding across docs
                    col_name = f"{doc_prefix}_{nk}" if field_doc_counts.get(nk, 0) > 1 else nk
                    if col_name not in target_row:
                        target_row[col_name] = _clean_val(v)

    # Order columns: join keys first, then remaining attributes alphabetically
    all_cols: List[str] = list(norm_join_keys)
    other_cols_set: Set[str] = set()
    for row in merged_map.values():
        for col in row.keys():
            if col not in all_cols:
                other_cols_set.add(col)
    all_cols.extend(sorted(other_cols_set))

    truncated = len(row_order) > max_merged_rows
    selected_keys = row_order[:max_merged_rows]

    col_defs = [
        {"name": c, "friendly_name": c.replace("_", " ").title(), "type": "string"}
        for c in all_cols
    ]

    rows = []
    for m_id in selected_keys:
        row_dict = merged_map[m_id]
        rows.append([row_dict.get(c, "") for c in all_cols])

    return col_defs, rows, truncated
