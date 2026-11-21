"""
Company Files CSV Parser (app/infrastructure/company_files/csv_parser.py)
Standard-library-only CSV parser for preview, type inference, column sanitization, and row ingestion.
No pandas, no FastAPI, no DB libraries.
"""

import csv
from datetime import datetime, timezone
import io
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.exceptions import AppError

RESERVED_NAMES: Set[str] = {"company_id", "file_id", "import_id", "_id"}

# Pattern for personal data heuristics
EMAIL_REGEX = re.compile(r"^[\w.-]+@[\w.-]+\.\w+$")
# 10 digits with optional +91, 0, spaces, dashes
PHONE_REGEX = re.compile(r"^(?:\+?91[\s-]?)?[0]?[6-9]\d{9}$")
# 12 digits optionally formatted with spaces or dashes
AADHAAR_REGEX = re.compile(r"^\d{4}[\s-]?\d{4}[\s-]?\d{4}$")
# Indian PAN: 5 letters, 4 digits, 1 letter
PAN_REGEX = re.compile(r"^[A-Za-z]{5}\d{4}[A-Za-z]$")

PERSONAL_HEADER_WORDS = {
    "password", "otp", "card", "account number", "account_number",
    "ifsc", "aadhaar", "aadhar", "pan", "dob", "phone", "mobile", "email",
}


class FileValidationError(AppError):
    def __init__(self, message: str, status_code: int = 400, code: str = "VALIDATION_ERROR", errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(code=code, message=message, http_status=status_code)
        self.errors = errors or []


def sanitize_header_to_key(header: str, seen_keys: Dict[str, int]) -> str:
    """
    Header -> key: lowercase, non-alphanumerics to '_', collapse repeats,
    must start with a letter (else prefix 'col_'), max 40 chars, duplicates get _2, _3.
    Reserved names get suffix '_value'. Keys never contain '$' or '.'.
    """
    raw = header.strip()
    raw_first_is_letter = bool(raw and raw[0].isalpha())
    # Replace non-alphanumerics with '_'
    s = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    # Collapse repeat underscores
    s = re.sub(r"_+", "_", s)

    if not s:
        s = "col"
    elif not raw_first_is_letter or not s[0].isalpha():
        s = f"col_{s}".strip("_")

    if s in RESERVED_NAMES:
        s = f"{s}_value"

    # Truncate to 40 chars
    s = s[:40].rstrip("_")
    if not s:
        s = "col"

    # Handle duplicates
    if s not in seen_keys:
        seen_keys[s] = 1
        return s
    else:
        seen_keys[s] += 1
        suffix = f"_{seen_keys[s]}"
        max_base_len = 40 - len(suffix)
        candidate = f"{s[:max_base_len].rstrip('_')}{suffix}"
        while candidate in seen_keys:
            seen_keys[s] += 1
            suffix = f"_{seen_keys[s]}"
            max_base_len = 40 - len(suffix)
            candidate = f"{s[:max_base_len].rstrip('_')}{suffix}"
        seen_keys[candidate] = 1
        return candidate


def try_parse_boolean(val_str: str) -> Optional[bool]:
    s = val_str.strip().lower()
    if s in ("true", "yes", "1"):
        return True
    if s in ("false", "no", "0"):
        return False
    return None


def try_parse_number(val_str: str) -> Optional[float | int]:
    s = val_str.strip()
    if not s:
        return None
    # Strip currency symbols and common currency keywords: ₹, $, €, £, ¥, Rs., INR, USD, etc.
    s = re.sub(r"^[₹$€£¥\s]+", "", s)
    s = re.sub(r"[₹$€£¥\s]+$", "", s)
    s = re.sub(r"^(?:rs\.?|inr|usd|eur|gbp)\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*(?:rs\.?|inr|usd|eur|gbp)$", "", s, flags=re.IGNORECASE)
    # Strip thousands commas e.g. 1,234,567.89
    clean = s.replace(",", "").strip()
    if re.match(r"^[+-]?\d+$", clean):
        try:
            return int(clean)
        except ValueError:
            return None
    if re.match(r"^[+-]?\d+\.\d+$", clean):
        try:
            return float(clean)
        except ValueError:
            return None
    return None


def try_parse_date(val_str: str) -> Optional[datetime]:
    s = val_str.strip()
    if not s:
        return None

    # 1. Try ISO datetime / date
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            cleaned = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass

    # 2. Try dd/mm/yyyy day-first
    for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y", "%d-%m-%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def check_is_personal(header_label: str, sample_values: List[str]) -> bool:
    # 1. Check header
    header_lower = header_label.lower()
    for word in PERSONAL_HEADER_WORDS:
        if word in header_lower:
            return True

    # 2. Check sample values
    for v in sample_values:
        clean = v.strip()
        if not clean:
            continue
        if EMAIL_REGEX.match(clean):
            return True
        if PHONE_REGEX.match(clean):
            return True
        if AADHAAR_REGEX.match(clean):
            return True
        if PAN_REGEX.match(clean):
            return True

    return False


class CompanyFilesCsvParser:
    """Standard-library CSV parser implementing company files preview and ingestion specifications."""

    def _decode_bytes(self, content_bytes: bytes, max_mb: int = 5) -> str:
        if len(content_bytes) > max_mb * 1024 * 1024:
            raise FileValidationError(
                f"File size exceeds maximum allowed limit of {max_mb} MB.",
                status_code=413,
                code="PAYLOAD_TOO_LARGE",
            )
        if not content_bytes or not content_bytes.strip():
            raise FileValidationError("Uploaded CSV file is empty.", status_code=400, code="EMPTY_FILE")

        if b"\x00" in content_bytes[:8192]:
            raise FileValidationError("Invalid binary file format. Please upload a plain CSV.", status_code=400, code="INVALID_FILE_FORMAT")

        for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                return content_bytes.decode(enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise FileValidationError("Could not decode file with supported text encodings.", status_code=400, code="DECODE_ERROR")

    def parse_preview(
        self,
        content_bytes: bytes,
        filename: str,
        max_files: int = 10,
        max_mb: int = 5,
        max_rows: int = 50000,
        max_columns: int = 30,
        max_cell_chars: int = 2000,
        max_preview_errors: int = 500,
    ) -> Dict[str, Any]:
        text = self._decode_bytes(content_bytes, max_mb)
        reader = csv.reader(io.StringIO(text))

        try:
            raw_headers = next(reader)
        except StopIteration:
            raise FileValidationError("Uploaded CSV file is empty.", status_code=400, code="EMPTY_FILE")

        if not raw_headers:
            raise FileValidationError("CSV contains no headers.", status_code=400, code="NO_HEADERS")

        if len(raw_headers) > max_columns:
            raise FileValidationError(
                f"File contains {len(raw_headers)} columns, exceeding maximum limit of {max_columns}.",
                status_code=400,
                code="TOO_MANY_COLUMNS",
            )

        seen_keys: Dict[str, int] = {}
        columns_meta: List[Dict[str, Any]] = []
        col_keys: List[str] = []
        for idx, h in enumerate(raw_headers):
            key = sanitize_header_to_key(h, seen_keys)
            col_keys.append(key)
            columns_meta.append({
                "key": key,
                "label": h.strip() if h.strip() else key,
                "index": idx,
                "values": [],
            })

        # Read rows
        raw_rows: List[List[str]] = []
        row_count = 0
        for row in reader:
            # Skip empty lines
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            row_count += 1
            if row_count > max_rows:
                raise FileValidationError(
                    f"File exceeds maximum allowed rows ({max_rows}).",
                    status_code=400,
                    code="TOO_MANY_ROWS",
                )
            raw_rows.append(row)

        if not raw_rows:
            raise FileValidationError("CSV contains no data rows.", status_code=400, code="EMPTY_FILE")

        # Collect cell values per column
        for row in raw_rows:
            for idx, col in enumerate(columns_meta):
                val = row[idx].strip() if idx < len(row) else ""
                col["values"].append(val)

        # Infer type and suggested_hidden for each column
        detected_columns: List[Dict[str, Any]] = []
        for col in columns_meta:
            vals = col["values"]
            non_empty = [v for v in vals if v]
            total_non_empty = len(non_empty)

            inferred_type = "text"
            if total_non_empty > 0:
                # 1. Test boolean: all non-empty must fit
                bool_count = sum(1 for v in non_empty if try_parse_boolean(v) is not None)
                if bool_count == total_non_empty:
                    inferred_type = "boolean"
                else:
                    # 2. Test number: >= 95%
                    num_count = sum(1 for v in non_empty if try_parse_number(v) is not None)
                    if (num_count / total_non_empty) >= 0.95:
                        inferred_type = "number"
                    else:
                        # 3. Test date: >= 95%
                        date_count = sum(1 for v in non_empty if try_parse_date(v) is not None)
                        if (date_count / total_non_empty) >= 0.95:
                            inferred_type = "date"
                        else:
                            inferred_type = "text"

            # Find first non-empty example
            example = non_empty[0] if non_empty else None
            # Check suggested hidden
            suggested_hidden = check_is_personal(col["label"], non_empty[:20])

            detected_columns.append({
                "key": col["key"],
                "label": col["label"],
                "type": inferred_type,
                "example": example,
                "suggested_hidden": suggested_hidden,
            })

        # Validate rows according to inferred column types and max_cell_chars
        errors: List[Dict[str, Any]] = []
        valid_rows_count = 0
        invalid_rows_count = 0

        for row_idx, row in enumerate(raw_rows, start=2):  # Row 1 is header
            row_is_valid = True
            for col_idx, col_def in enumerate(detected_columns):
                val = row[col_idx].strip() if col_idx < len(row) else ""
                if not val:
                    continue
                if len(val) > max_cell_chars:
                    row_is_valid = False
                    if len(errors) < max_preview_errors:
                        errors.append({
                            "row": row_idx,
                            "column": col_def["key"],
                            "reason": f"Cell exceeds maximum character limit of {max_cell_chars}.",
                        })
                    break

                # Type validity check
                c_type = col_def["type"]
                if c_type == "number" and try_parse_number(val) is None:
                    row_is_valid = False
                    if len(errors) < max_preview_errors:
                        errors.append({
                            "row": row_idx,
                            "column": col_def["key"],
                            "reason": f"Value '{val[:30]}' is not a valid number.",
                        })
                    break
                elif c_type == "date" and try_parse_date(val) is None:
                    row_is_valid = False
                    if len(errors) < max_preview_errors:
                        errors.append({
                            "row": row_idx,
                            "column": col_def["key"],
                            "reason": f"Value '{val[:30]}' is not a valid date.",
                        })
                    break
                elif c_type == "boolean" and try_parse_boolean(val) is None:
                    row_is_valid = False
                    if len(errors) < max_preview_errors:
                        errors.append({
                            "row": row_idx,
                            "column": col_def["key"],
                            "reason": f"Value '{val[:30]}' is not a valid boolean.",
                        })
                    break

            if row_is_valid:
                valid_rows_count += 1
            else:
                invalid_rows_count += 1

        # Build sample_rows (first 5 raw data rows, mapped to sanitized keys, hidden not removed)
        sample_rows: List[Dict[str, Any]] = []
        for r in raw_rows[:5]:
            row_dict = {}
            for idx, col_def in enumerate(detected_columns):
                val = r[idx].strip() if idx < len(r) else ""
                row_dict[col_def["key"]] = val if val else None
            sample_rows.append(row_dict)

        return {
            "filename": filename,
            "total_rows": row_count,
            "valid_rows": valid_rows_count,
            "invalid_rows": invalid_rows_count,
            "columns": detected_columns,
            "sample_rows": sample_rows,
            "errors": errors,
            "limits": {
                "max_files": max_files,
                "max_mb": max_mb,
                "max_rows": max_rows,
                "max_columns": max_columns,
            },
        }

    def parse_for_ingestion(
        self,
        content_bytes: bytes,
        columns: List[Dict[str, Any]],
        max_mb: int = 5,
        max_rows: int = 50000,
        max_columns: int = 30,
        max_cell_chars: int = 2000,
        max_errors: int = 500,
    ) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]], List[str]]:
        """
        Parses CSV against defined columns.
        Returns (valid_rows, skipped_count, errors, ignored_columns)
        """
        text = self._decode_bytes(content_bytes, max_mb)
        reader = csv.reader(io.StringIO(text))

        try:
            raw_headers = next(reader)
        except StopIteration:
            raise FileValidationError("Uploaded CSV file is empty.", status_code=400, code="EMPTY_FILE")

        if not raw_headers:
            raise FileValidationError("CSV contains no headers.", status_code=400, code="NO_HEADERS")

        seen_keys: Dict[str, int] = {}
        header_key_to_idx: Dict[str, int] = {}
        for idx, h in enumerate(raw_headers):
            key = sanitize_header_to_key(h, seen_keys)
            header_key_to_idx[key] = idx

        # Verify that all required saved columns exist in the CSV headers
        saved_keys = [c["key"] for c in columns]
        missing_keys = [k for k in saved_keys if k not in header_key_to_idx]
        if missing_keys:
            raise FileValidationError(
                f"CSV is missing required saved columns: {', '.join(missing_keys)}",
                status_code=400,
                code="MISSING_COLUMNS",
            )

        ignored_columns = [k for k in header_key_to_idx.keys() if k not in saved_keys]

        # Key to type map
        key_type_map = {c["key"]: c.get("type", "text") for c in columns}

        valid_rows: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        skipped_count = 0
        row_idx = 1

        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            row_idx += 1
            if (row_idx - 1) > max_rows:
                raise FileValidationError(
                    f"File exceeds maximum allowed rows ({max_rows}).",
                    status_code=400,
                    code="TOO_MANY_ROWS",
                )

            row_data: Dict[str, Any] = {}
            row_is_valid = True

            for col in columns:
                k = col["key"]
                idx = header_key_to_idx[k]
                val = row[idx].strip() if idx < len(row) else ""

                if not val:
                    row_data[k] = None
                    continue

                if len(val) > max_cell_chars:
                    row_is_valid = False
                    if len(errors) < max_errors:
                        errors.append({
                            "row": row_idx,
                            "column": k,
                            "reason": f"Cell exceeds maximum character limit of {max_cell_chars}.",
                        })
                    break

                c_type = key_type_map.get(k, "text")
                if c_type == "number":
                    parsed_num = try_parse_number(val)
                    if parsed_num is None:
                        row_is_valid = False
                        if len(errors) < max_errors:
                            errors.append({
                                "row": row_idx,
                                "column": k,
                                "reason": f"Value '{val[:30]}' is not a valid number.",
                            })
                        break
                    row_data[k] = parsed_num
                elif c_type == "date":
                    parsed_date = try_parse_date(val)
                    if parsed_date is None:
                        row_is_valid = False
                        if len(errors) < max_errors:
                            errors.append({
                                "row": row_idx,
                                "column": k,
                                "reason": f"Value '{val[:30]}' is not a valid date.",
                            })
                        break
                    row_data[k] = parsed_date
                elif c_type == "boolean":
                    parsed_bool = try_parse_boolean(val)
                    if parsed_bool is None:
                        row_is_valid = False
                        if len(errors) < max_errors:
                            errors.append({
                                "row": row_idx,
                                "column": k,
                                "reason": f"Value '{val[:30]}' is not a valid boolean.",
                            })
                        break
                    row_data[k] = parsed_bool
                else:
                    row_data[k] = val

            if row_is_valid:
                valid_rows.append(row_data)
            else:
                skipped_count += 1

        return valid_rows, skipped_count, errors, ignored_columns
