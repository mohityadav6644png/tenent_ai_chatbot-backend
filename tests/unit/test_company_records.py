"""
Unit Tests for Company Records Logic (tests/unit/test_company_records.py)
"""

import io
import pytest

from app.infrastructure.company.csv_parser import CsvParseError, StandardCsvParser
from app.modules.company.service import CompanyDataError, CompanyService


class MockRecordsRepo:
    def __init__(self):
        self.records = []

    async def insert_record(self, doc):
        self.records.append(doc)
        return "rec_001"

    async def bulk_insert_records(self, docs):
        self.records.extend(docs)
        return len(docs)


class MockTrackingRepo:
    def __init__(self):
        self.imports = []

    async def has_active_import(self, company_id, dataset, timeout_minutes=15):
        return False

    async def count_recent_imports(self, company_id, hours=1):
        return 0

    async def create_import(self, doc):
        self.imports.append(doc)
        return "imp_001"

    async def update_import(self, import_id, updates):
        pass


@pytest.mark.asyncio
async def test_manual_record_validation():
    repo = MockRecordsRepo()
    svc = CompanyService(records_repo=repo)

    # Empty title
    with pytest.raises(CompanyDataError) as exc_info:
        await svc.insert_manual_record(company_id="c1", title="   ", content="Valid content")
    assert exc_info.value.code == "VALIDATION_ERROR"

    # Title exceeds 200 chars
    with pytest.raises(CompanyDataError) as exc_info:
        await svc.insert_manual_record(company_id="c1", title="T" * 201, content="Valid content")
    assert exc_info.value.code == "VALIDATION_ERROR"

    # Content exceeds 20,000 chars
    with pytest.raises(CompanyDataError) as exc_info:
        await svc.insert_manual_record(company_id="c1", title="Valid Title", content="C" * 20001)
    assert exc_info.value.code == "VALIDATION_ERROR"

    # Control characters in title
    with pytest.raises(CompanyDataError) as exc_info:
        await svc.insert_manual_record(company_id="c1", title="Bad\x00Title", content="Valid content")
    assert exc_info.value.code == "VALIDATION_ERROR"

    # Valid insert
    rec_id = await svc.insert_manual_record(
        company_id="c1",
        title="Valid Title\tWith Tabs",
        content="Valid Content\nWith Newlines",
        user_id="user_123",
    )
    assert rec_id == "rec_001"
    assert len(repo.records) == 1
    assert repo.records[0]["company_id"] == "c1"
    assert repo.records[0]["created_by"] == "user_123"


def test_standard_csv_parser_validations():
    parser = StandardCsvParser()

    # 1. Empty bytes
    with pytest.raises(CsvParseError) as exc:
        parser.parse_csv_stream(b"")
    assert exc.value.code == "EMPTY_FILE"

    # 2. Binary file with null bytes
    with pytest.raises(CsvParseError) as exc:
        parser.parse_csv_stream(b"PK\x03\x04\x00\x00\x00Some binary file")
    assert exc.value.code == "INVALID_FILE_FORMAT"

    # 3. Duplicate headers
    with pytest.raises(CsvParseError) as exc:
        parser.parse_csv_stream(b"Title,title\nval1,val2")
    assert exc.value.code == "DUPLICATE_COLUMNS"

    # 4. Invalid header regex (special characters)
    with pytest.raises(CsvParseError) as exc:
        parser.parse_csv_stream(b"Title$,Content\nval1,val2")
    assert exc.value.code == "INVALID_COLUMN_HEADER"

    # 5. Cell limit exceeded
    with pytest.raises(CsvParseError) as exc:
        parser.parse_csv_stream(b"Title,Content\nT," + b"C" * 2001, max_cell_chars=2000)
    assert exc.value.code == "CELL_LIMIT_EXCEEDED"

    # 6. Strips system columns (company_id, _id, created_at, created_by)
    csv_data = b"company_id,Title,Content,_id,created_at\nfake_corp,My Title,My Content,id1,date1"
    headers, rows, skipped = parser.parse_csv_stream(csv_data)
    assert "company_id" not in headers
    assert "_id" not in headers
    assert "created_at" not in headers
    assert headers == ["Title", "Content"]
    assert len(rows) == 1
    assert rows[0]["Title"] == "My Title"


@pytest.mark.asyncio
async def test_csv_import_missing_columns_details():
    repo = MockRecordsRepo()
    tracking = MockTrackingRepo()
    parser = StandardCsvParser()
    svc = CompanyService(records_repo=repo, tracking_repo=tracking, csv_parser=parser)

    # Missing title and content
    csv_bytes = b"Author,Department,Salary\nAlice,Engineering,5000\n"
    with pytest.raises(CompanyDataError) as exc_info:
        await svc.import_csv_data(company_id="c1", content_bytes=csv_bytes)

    err = exc_info.value
    assert err.code == "MISSING_COLUMNS"
    assert err.status_code == 400
    assert err.details is not None
    assert "found_columns" in err.details
    assert err.details["found_columns"] == ["Author", "Department", "Salary"]
    # Ensure no row values leaked in details
    assert "Alice" not in str(err.details)


def test_standard_csv_parser_trailing_commas_and_delimiters():
    parser = StandardCsvParser()

    # Trailing comma in header line (e.g. from Excel or CSV tool export)
    csv_trailing = b"Title,Content,\nTest Title,Test Content,\n"
    headers, rows, skipped = parser.parse_csv_stream(csv_trailing)
    assert headers == ["Title", "Content"]
    assert len(rows) == 1
    assert rows[0]["Title"] == "Test Title"
    assert rows[0]["Content"] == "Test Content"

    # Semicolon-delimited CSV
    csv_semi = b"Title;Content\nSemi Title;Semi Content\n"
    headers2, rows2, _ = parser.parse_csv_stream(csv_semi)
    assert headers2 == ["Title", "Content"]
    assert rows2[0]["Title"] == "Semi Title"


@pytest.mark.asyncio
async def test_csv_import_auto_detection_and_two_columns():
    repo = MockRecordsRepo()
    tracking = MockTrackingRepo()
    parser = StandardCsvParser()
    svc = CompanyService(records_repo=repo, tracking_repo=tracking, csv_parser=parser)

    # 1. Question, Answer
    qa_csv = b"Question,Answer\nWhat is your refund policy?,30 days return window.\n"
    count, skipped, _ = await svc.import_csv_data(company_id="c1", content_bytes=qa_csv)
    assert count == 1
    assert repo.records[-1]["title"] == "What is your refund policy?"
    assert repo.records[-1]["content"] == "30 days return window."

    # 2. Arbitrary 2-column fallback
    two_col = b"HeaderOne,HeaderTwo\nValue 1,Value 2\n"
    count2, _, _ = await svc.import_csv_data(company_id="c1", content_bytes=two_col)
    assert count2 == 1
    assert repo.records[-1]["title"] == "Value 1"
    assert repo.records[-1]["content"] == "Value 2"
