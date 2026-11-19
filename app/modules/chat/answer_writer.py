"""
Answer Writer Sanitizer & Helper (app/modules/chat/answer_writer.py)
Sanitizes query result rows by stripping sensitive columns and capping rows/chars before passing to an LLM answer writer.
"""

from typing import Any, Dict, List, Tuple
from app.core.config import settings

SENSITIVE_FIELD_NAMES = {
    "email", "phone", "password", "ssn", "token", "secret", "api_key",
    "access_token", "refresh_token", "hash", "salt",
}


def sanitize_for_writer(
    columns: List[Dict[str, str]],
    rows: List[List[Any]],
    max_rows: int = 10,
    max_chars_per_val: int = 100,
) -> Tuple[List[str], List[List[Any]]]:
    """Filter out sensitive column names and truncate rows and string values."""
    safe_indices: List[int] = []
    safe_col_names: List[str] = []

    for idx, col in enumerate(columns):
        c_name = col.get("name", "").lower()
        if not any(sens in c_name for sens in SENSITIVE_FIELD_NAMES):
            safe_indices.append(idx)
            safe_col_names.append(col.get("name", f"col_{idx}"))

    safe_rows: List[List[Any]] = []
    for row in rows[:max_rows]:
        safe_row = []
        for idx in safe_indices:
            val = row[idx] if idx < len(row) else None
            if isinstance(val, str) and len(val) > max_chars_per_val:
                val = val[:max_chars_per_val] + "..."
            safe_row.append(val)
        safe_rows.append(safe_row)

    return safe_col_names, safe_rows
