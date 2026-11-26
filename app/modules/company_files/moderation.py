"""
Company Files Moderation and Data Health (app/modules/company_files/moderation.py)
Pure Python upload moderation, data health warnings, personal data detection,
formula safety, and prompt-injection masking. Strictly no DB/fastapi/csv imports.
"""

import re
from typing import Any, Dict, List, Optional, Set

IFSC_REGEX = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
PASSPORT_REGEX = re.compile(r"^[A-Z][0-9]{7}$")
VOTER_ID_REGEX = re.compile(r"^[A-Z]{3}[0-9]{7}$")
DOB_HEADER_REGEX = re.compile(r"\b(dob|birth|birthdate|date_of_birth)\b", re.I)

INSTRUCTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I),
    re.compile(r"system\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\b", re.I),
    re.compile(r"\bact\s+as\b", re.I),
    re.compile(r"developer\s+message", re.I),
    re.compile(r"\breveal\b.*(?:prompt|instructions|secret)", re.I),
    re.compile(r"<script[\s>]|<!--|\{\{.*?\}\}", re.I),
]


def check_luhn(val: str) -> bool:
    digits = re.sub(r"\D", "", val)
    if len(digits) not in (13, 14, 15, 16, 19):
        return False
    total = 0
    reverse_digits = digits[::-1]
    for i, char in enumerate(reverse_digits):
        d = int(char)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def is_formula_cell(val: str) -> bool:
    s = val.strip()
    return bool(s and s[0] in ("=", "+", "-", "@"))


def is_instruction_like(val: str) -> bool:
    if not val:
        return False
    s = str(val).strip()
    for pat in INSTRUCTION_PATTERNS:
        if pat.search(s):
            return True
    # Very long single token check (> 100 chars without whitespace)
    tokens = s.split()
    if any(len(t) > 100 for t in tokens):
        return True
    return False


def is_personal_value(val: str) -> bool:
    s = val.strip()
    if not s:
        return False
    if check_luhn(s):
        return True
    if IFSC_REGEX.match(s.upper()):
        return True
    if PASSPORT_REGEX.match(s.upper()):
        return True
    if VOTER_ID_REGEX.match(s.upper()):
        return True
    return False


def review_data_health(
    columns: List[Dict[str, Any]],
    raw_rows: List[List[str]],
    invalid_rows_count: int = 0,
    max_flagged_ratio: float = 0.02,
) -> Dict[str, Any]:
    total_rows = len(raw_rows)
    warnings: List[Dict[str, Any]] = []
    formula_count = 0
    instruction_count = 0
    flagged_cols: Dict[str, Dict[str, Any]] = {}
    personal_cols: Set[str] = set()
    empty_cols: Set[str] = set()

    # Track duplicate rows
    seen_row_hashes: Set[int] = set()
    duplicate_rows_count = 0
    for r in raw_rows:
        h = hash(tuple(r))
        if h in seen_row_hashes:
            duplicate_rows_count += 1
        else:
            seen_row_hashes.add(h)

    for col_idx, col in enumerate(columns):
        key = col["key"]
        label = col.get("label", key)
        col_inst_count = 0
        col_formula_count = 0
        non_empty = 0

        if DOB_HEADER_REGEX.search(label) or DOB_HEADER_REGEX.search(key):
            personal_cols.add(key)

        for row in raw_rows:
            val = row[col_idx].strip() if col_idx < len(row) else ""
            if not val:
                continue
            non_empty += 1
            if is_formula_cell(val):
                col_formula_count += 1
            if is_instruction_like(val):
                col_inst_count += 1
            if not col.get("suggested_hidden") and is_personal_value(val):
                personal_cols.add(key)

        if non_empty == 0 and total_rows > 0:
            empty_cols.add(key)

        formula_count += col_formula_count
        instruction_count += col_inst_count
        ratio = (col_inst_count / max(non_empty, 1)) if non_empty > 0 else 0.0
        flagged_cols[key] = {
            "instruction_count": col_inst_count,
            "formula_count": col_formula_count,
            "flagged_ratio": round(ratio, 4),
            "exclude_from_catalog": ratio > max_flagged_ratio,
        }

    if formula_count > 0:
        warnings.append({
            "type": "formula_cells",
            "count": formula_count,
            "message": f"{formula_count} formula-like cells detected (safely stored, prefixed with ' on export).",
        })
    if instruction_count > 0:
        warnings.append({
            "type": "instruction_like",
            "count": instruction_count,
            "message": f"{instruction_count} instruction-like cells excluded from AI catalog.",
        })
    if personal_cols:
        warnings.append({
            "type": "personal_data",
            "count": len(personal_cols),
            "columns": sorted(list(personal_cols)),
            "message": f"{len(personal_cols)} columns look like personal data.",
        })
    if empty_cols:
        warnings.append({
            "type": "empty_columns",
            "count": len(empty_cols),
            "columns": sorted(list(empty_cols)),
            "message": f"{len(empty_cols)} columns are completely empty.",
        })
    if duplicate_rows_count > 0:
        warnings.append({
            "type": "duplicate_rows",
            "count": duplicate_rows_count,
            "message": f"{duplicate_rows_count} fully duplicated rows found.",
        })
    if total_rows > 0 and (invalid_rows_count / total_rows) > 0.1:
        warnings.append({
            "type": "high_invalid_rows",
            "count": invalid_rows_count,
            "message": f"{invalid_rows_count} rows ({round(invalid_rows_count/total_rows*100, 1)}%) could not be parsed.",
        })

    return {
        "status": "clean" if not warnings else "warning",
        "warnings": warnings,
        "flagged_columns": flagged_cols,
        "duplicate_rows": duplicate_rows_count,
        "formula_cells_count": formula_count,
        "instruction_cells_count": instruction_count,
        "personal_columns": sorted(list(personal_cols)),
    }
