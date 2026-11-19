"""
Response Builder (app/modules/chat/response_builder.py)
Pure Python deterministic template filler, pct calculation, and chart hint detection.
"""

import re
from typing import Any, Dict, List, Optional


def fill_template(
    template: Optional[str],
    columns: List[Dict[str, str]],
    rows: List[List[Any]],
    fallback: str = "Results for your question",
) -> str:
    """Safely populate headline/detail templates with {row1.<col>}, {row2.<col>}, {pct_1_2}, {row_count}."""
    if not template or not template.strip():
        return fallback

    row_count = len(rows)
    col_idx = {col["name"].lower(): idx for idx, col in enumerate(columns)}

    # Precompute pct_1_2 if there are at least two rows and numbers
    pct_val = "N/A"
    if row_count >= 2:
        try:
            # Look for first numeric column in row 1 and row 2
            v1, v2 = None, None
            for idx in range(len(columns)):
                try:
                    val1 = float(rows[0][idx])
                    val2 = float(rows[1][idx])
                    v1, v2 = val1, val2
                    break
                except (ValueError, TypeError):
                    continue
            if v1 is not None and v2 is not None and v2 != 0:
                diff = ((v1 - v2) / abs(v2)) * 100
                pct_val = f"{diff:+.1f}%"
        except Exception:
            pct_val = "N/A"

    def replace_placeholder(match):
        token = match.group(1).strip()
        if token == "row_count":
            return str(row_count)
        if token == "pct_1_2":
            return pct_val

        # Check row1.<col> and row2.<col>
        if token.startswith("row1."):
            col_name = token[5:].lower()
            if col_name in col_idx and row_count >= 1:
                return str(rows[0][col_idx[col_name]])
            raise ValueError(f"Missing row or column for {token}")

        if token.startswith("row2."):
            col_name = token[5:].lower()
            if col_name in col_idx and row_count >= 2:
                return str(rows[1][col_idx[col_name]])
            raise ValueError(f"Missing row or column for {token}")

        raise ValueError(f"Unknown placeholder: {token}")

    try:
        return re.sub(r"\{([^}]+)\}", replace_placeholder, template)
    except Exception:
        return fallback


def determine_chart_hint(columns: List[Dict[str, str]], rows: List[List[Any]]) -> str:
    """Deterministically determine chart_hint: 'none', 'line', or 'bar'."""
    if len(rows) < 2 or len(columns) < 2:
        return "none"

    has_date = False
    has_numeric = False
    has_category = False

    for c in columns:
        name = c["name"].lower()
        if any(d in name for d in ["date", "time", "day", "month", "year"]):
            has_date = True
        elif any(n in name for n in ["amount", "total", "spent", "revenue", "price", "count", "units", "quantity", "val"]):
            has_numeric = True
        else:
            has_category = True

    if has_date and has_numeric:
        return "line"
    if has_category and has_numeric:
        return "bar"
    return "bar"
