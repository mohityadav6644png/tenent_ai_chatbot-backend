"""
Standard Library CSV Parser (app/infrastructure/company/csv_parser.py)
Parses, sanitizes, and validates CSV streams using only Python's standard csv module.
"""

import csv
import io
import re
from typing import Dict, List, Set, Tuple

HEADER_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_ \-()/?#]{0,60}$")
SYSTEM_COLUMNS_TO_STRIP: Set[str] = {"company_id", "_id", "created_at", "created_by"}


class CsvParseError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "BAD_REQUEST", details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details


class StandardCsvParser:
    """CSV parser implementation strictly adhering to stdlib csv and project safety constraints."""

    def parse_csv_stream(
        self,
        content_bytes: bytes,
        max_file_mb: int = 5,
        max_rows: int = 50000,
        max_columns: int = 30,
        max_cell_chars: int = 2000,
    ) -> Tuple[List[str], List[Dict[str, str]], int]:
        """
        Parses raw bytes into headers and row dictionaries.
        Returns:
            (headers, list_of_row_dicts, skipped_count)
        """
        # 1. Size check
        if len(content_bytes) > max_file_mb * 1024 * 1024:
            raise CsvParseError(
                f"File size exceeds maximum allowed limit of {max_file_mb}MB.",
                status_code=413,
                code="PAYLOAD_TOO_LARGE",
            )

        if not content_bytes or len(content_bytes.strip()) == 0:
            raise CsvParseError("Uploaded CSV file is empty.", status_code=400, code="EMPTY_FILE")

        # 2. Reject binary content
        # Check first 8KB for null bytes
        check_slice = content_bytes[:8192]
        if b"\x00" in check_slice:
            raise CsvParseError(
                "Invalid file format. Please upload a valid CSV file.",
                status_code=400,
                code="INVALID_FILE_FORMAT",
            )

        # 3. Decode byte stream
        text = None
        for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                text = content_bytes.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if text is None:
            raise CsvParseError(
                "Could not decode CSV file. Unsupported text encoding.",
                status_code=400,
                code="INVALID_ENCODING",
            )

        text = text.lstrip("\ufeff")

        # 4. Read headers and validate
        # Auto-detect delimiter if first line has ; or \t instead of ,
        first_line = text.splitlines()[0] if text.splitlines() else ""
        delimiter = ","
        if "," not in first_line:
            if ";" in first_line:
                delimiter = ";"
            elif "\t" in first_line:
                delimiter = "\t"
            elif "|" in first_line:
                delimiter = "|"

        stream = io.StringIO(text)
        reader = csv.reader(stream, delimiter=delimiter)

        try:
            raw_headers = next(reader)
        except StopIteration:
            raise CsvParseError("Uploaded CSV file is empty.", status_code=400, code="EMPTY_FILE")

        if not raw_headers:
            raise CsvParseError("CSV file contains no column headers.", status_code=400, code="EMPTY_HEADERS")

        # Strip whitespace and quotes
        headers = [h.strip().strip('"\'') for h in raw_headers]

        # Strip trailing empty headers (from trailing commas or empty trailing cells in CSV exports)
        while headers and not headers[-1]:
            headers.pop()

        if not headers:
            raise CsvParseError("CSV file contains no column headers.", status_code=400, code="EMPTY_HEADERS")

        # Check column count
        if len(headers) > max_columns:
            raise CsvParseError(
                f"CSV exceeds maximum allowed columns ({max_columns}). Found {len(headers)}.",
                status_code=400,
                code="MAX_COLUMNS_EXCEEDED",
            )

        # Validate headers format and uniqueness
        seen_lower = set()
        cleaned_headers: List[str] = []
        for idx, h in enumerate(headers):
            if not h:
                h = f"column_{idx + 1}"
            h_lower = h.lower()
            if h_lower in seen_lower:
                raise CsvParseError(
                    f"Duplicate column header found: '{h}'.",
                    status_code=400,
                    code="DUPLICATE_COLUMNS",
                )
            seen_lower.add(h_lower)

            # Ignore system columns from regex check
            if h_lower in SYSTEM_COLUMNS_TO_STRIP:
                cleaned_headers.append(h)
                continue

            if not HEADER_REGEX.match(h):
                raise CsvParseError(
                    f"Invalid column header: '{h}'. Column names must start with a letter and contain only letters, numbers, spaces, underscores, or hyphens (max 60 chars).",
                    status_code=400,
                    code="INVALID_COLUMN_HEADER",
                )
            cleaned_headers.append(h)
        headers = cleaned_headers

        # 5. Read rows
        rows: List[Dict[str, str]] = []
        skipped_count = 0
        total_data_rows = 0

        for row_idx, row in enumerate(reader, start=2):
            total_data_rows += 1
            if total_data_rows > max_rows:
                raise CsvParseError(
                    f"CSV exceeds maximum allowed rows ({max_rows}).",
                    status_code=400,
                    code="MAX_ROWS_EXCEEDED",
                )

            # Check if entire row is empty (considering up to number of headers)
            row_slice = row[: len(headers)]
            cleaned_cells = [cell.strip() for cell in row_slice]
            if not any(cleaned_cells):
                skipped_count += 1
                continue

            # Check cell char lengths
            for cell_val in cleaned_cells:
                if len(cell_val) > max_cell_chars:
                    raise CsvParseError(
                        f"Row {row_idx} contains a cell exceeding maximum limit of {max_cell_chars} characters.",
                        status_code=400,
                        code="CELL_LIMIT_EXCEEDED",
                    )

            # Map to header dict
            row_dict: Dict[str, str] = {}
            for col_idx, h in enumerate(headers):
                val = cleaned_cells[col_idx] if col_idx < len(cleaned_cells) else ""
                # Ignore system columns
                if h.lower() not in SYSTEM_COLUMNS_TO_STRIP:
                    row_dict[h] = val

            rows.append(row_dict)

        # Filter out system columns from headers returned
        safe_headers = [h for h in headers if h.lower() not in SYSTEM_COLUMNS_TO_STRIP]

        return safe_headers, rows, skipped_count

    def generate_template_csv(self, columns: List[str]) -> str:
        """Generate CSV string containing only the header row."""
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(columns)
        return output.getvalue()
