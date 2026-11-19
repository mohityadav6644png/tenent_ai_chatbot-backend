"""
Integration Tests for Company Data Ingestion Endpoints (tests/integration/test_company_data_endpoints.py)
Tests:
- POST /api/v1/company/data/insert (Manual Text Insertion)
- POST /api/v1/company/import-csv-data (CSV Import & Column Mapping)
- JWT role validation (company_admin vs employee, super_admin)
- Automatic extraction and tenant isolation stamping (company_id)
- Frontend payload tampering prevention (payload company_id ignored)
- Column mapping layer heuristics and custom mappings
- Error handling (403 for unauthorized, 400 for bad files/missing columns)
"""

import io
from bson import ObjectId
import httpx
import pytest
from app.api.deps import get_db
from app.core.security import create_access_token
from app.main import app


class MockRecordsCollection:
    def __init__(self):
        self.records = []

    async def insert_one(self, doc):
        doc_copy = dict(doc)
        if "_id" not in doc_copy:
            doc_copy["_id"] = ObjectId()
        self.records.append(doc_copy)

        class InsertOneResult:
            inserted_id = doc_copy["_id"]

        return InsertOneResult()

    async def insert_many(self, docs, *args, **kwargs):
        inserted_ids = []
        for d in docs:
            doc_copy = dict(d)
            if "_id" not in doc_copy:
                doc_copy["_id"] = ObjectId()
            self.records.append(doc_copy)
            inserted_ids.append(doc_copy["_id"])

        class InsertManyResult:
            pass

        res = InsertManyResult()
        res.inserted_ids = inserted_ids
        return res


class MockTestDB:
    def __init__(self):
        self.records_collection = MockRecordsCollection()

    def __getitem__(self, name):
        if name == "company_records":
            return self.records_collection
        return MockRecordsCollection()


@pytest.fixture(autouse=True)
def mock_auth_repo(monkeypatch):
    class MockAuthRepo:
        async def get_user_by_id(self, user_id):
            return {"_id": user_id, "is_active": True}

        async def get_tenant_by_id(self, tenant_id):
            return {"_id": tenant_id, "status": "active"}

    monkeypatch.setattr("app.api.deps.get_auth_repo", lambda: MockAuthRepo())


@pytest.fixture
def mock_db():
    db = MockTestDB()
    app.dependency_overrides[get_db] = lambda: db
    yield db
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def admin_token():
    return create_access_token(
        user_id="admin_user_001",
        role="company_admin",
        company_id="acme_corp_999",
    )


@pytest.fixture
def employee_token():
    return create_access_token(
        user_id="emp_user_002",
        role="employee",
        company_id="acme_corp_999",
    )


@pytest.fixture
def superadmin_token():
    return create_access_token(
        user_id="super_user_003",
        role="super_admin",
        company_id="acme_corp_999",
    )


@pytest.fixture
def token_no_company():
    return create_access_token(
        user_id="admin_no_company",
        role="company_admin",
        company_id=None,
    )


# ---------------------------------------------------------------------------
# Manual Text Data Insertion Tests (POST /data/insert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_data_insert_success(mock_db, admin_token):
    """Company admin can insert text record; stamped with JWT company_id and UTC timestamp."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "title": "Quarterly Security Policy",
            "content": "All employees must enable multi-factor authentication.",
        }
        res = await client.post(
            "/api/v1/company/data/insert",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200, f"Expected 200 OK: {res.text}"
        data = res.json()
        assert data["message"] == "Record inserted successfully"
        assert "id" in data
        assert "record_id" in data
        assert data["id"] == data["record_id"]

        # Verify document stored in mock MongoDB
        assert len(mock_db.records_collection.records) == 1
        saved = mock_db.records_collection.records[0]
        assert saved["company_id"] == "acme_corp_999"
        assert saved["title"] == payload["title"]
        assert saved["content"] == payload["content"]
        assert saved["created_at"] is not None


@pytest.mark.asyncio
async def test_manual_data_insert_ignores_frontend_company_id(mock_db, admin_token):
    """Frontend-supplied company_id must be completely ignored in favor of the JWT token company_id."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "title": "Attempted Spoof Record",
            "content": "Attempting to write into competitor company.",
            "company_id": "malicious_spoofed_company_xyz",
        }
        res = await client.post(
            "/api/v1/company/data/insert",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        saved = mock_db.records_collection.records[0]
        # Must be stamped with the admin's verified company_id, NOT the spoofed payload
        assert saved["company_id"] == "acme_corp_999"
        assert saved["company_id"] != "malicious_spoofed_company_xyz"


@pytest.mark.asyncio
async def test_manual_data_insert_forbidden_for_employee(mock_db, employee_token):
    """Employee role is forbidden from inserting company data (HTTP 403)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/data/insert",
            json={"title": "Test", "content": "Content"},
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert res.status_code == 403
        assert len(mock_db.records_collection.records) == 0


@pytest.mark.asyncio
async def test_manual_data_insert_forbidden_for_superadmin(mock_db, superadmin_token):
    """Super admin role without company_admin is forbidden (HTTP 403)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/data/insert",
            json={"title": "Test", "content": "Content"},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_manual_data_insert_forbidden_when_token_lacks_company_id(mock_db, token_no_company):
    """Admin token lacking tenant stamp (company_id) is rejected (HTTP 403)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/data/insert",
            json={"title": "Test", "content": "Content"},
            headers={"Authorization": f"Bearer {token_no_company}"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_manual_data_insert_validation_error_empty_fields(mock_db, admin_token):
    """Empty or missing title/content returns validation error (HTTP 422 or 400)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Missing content
        res = await client.post(
            "/api/v1/company/data/insert",
            json={"title": "Just Title"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code in [400, 422]

        # Whitespace-only title
        res2 = await client.post(
            "/api/v1/company/data/insert",
            json={"title": "   ", "content": "Valid Content"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res2.status_code in [400, 422]


# ---------------------------------------------------------------------------
# CSV File Import & Column Mapping Tests (POST /import-csv-data)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_import_with_mapped_headers(mock_db, admin_token):
    """CSV with 'Document Title' and 'Body Content' headers maps correctly and imports all rows."""
    csv_content = (
        "Document Title,Body Content\n"
        "Employee Handbook,All policies and onboarding guidelines.\n"
        "IT Security Guide,Password requirements and VPN settings.\n"
        "Travel Expenses,Per diem and reimbursement procedures.\n"
    )
    files = {"file": ("docs.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-data",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200, f"Expected 200 OK: {res.text}"
        data = res.json()
        assert data["total_records_imported"] == 3
        assert data["success"] is True

        # Verify bulk insert in mock MongoDB
        assert len(mock_db.records_collection.records) == 3
        for rec in mock_db.records_collection.records:
            assert rec["company_id"] == "acme_corp_999"
            assert rec["created_at"] is not None
            assert len(rec["title"]) > 0
            assert len(rec["content"]) > 0

        assert mock_db.records_collection.records[0]["title"] == "Employee Handbook"
        assert mock_db.records_collection.records[1]["title"] == "IT Security Guide"
        assert mock_db.records_collection.records[2]["title"] == "Travel Expenses"


@pytest.mark.asyncio
async def test_csv_import_with_standard_headers(mock_db, admin_token):
    """CSV with 'title' and 'content' headers imports successfully."""
    csv_content = (
        "title,content\n"
        "Alpha Doc,Alpha content details.\n"
        "Beta Doc,Beta content details.\n"
    )
    files = {"file": ("standard.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-data",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_records_imported"] == 2
        assert len(mock_db.records_collection.records) == 2


@pytest.mark.asyncio
async def test_csv_import_with_custom_mapping(mock_db, admin_token):
    """CSV with arbitrary column names using custom mapping JSON."""
    csv_content = (
        "Headline,Article\n"
        "Breaking News,Something important happened.\n"
    )
    files = {"file": ("news.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    data_form = {"column_mapping": '{"Headline": "title", "Article": "content"}'}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-data",
            files=files,
            data=data_form,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        assert res.json()["total_records_imported"] == 1
        assert mock_db.records_collection.records[0]["title"] == "Breaking News"
        assert mock_db.records_collection.records[0]["content"] == "Something important happened."


@pytest.mark.asyncio
async def test_csv_import_missing_required_columns_fails(mock_db, admin_token):
    """CSV missing required 'content' column raises HTTP 400."""
    csv_content = (
        "title,author,date\n"
        "Article 1,John Doe,2026-01-01\n"
    )
    files = {"file": ("bad_headers.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-data",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 400
        assert "Missing required columns" in res.json()["error"]["message"]


@pytest.mark.asyncio
async def test_csv_import_bad_file_format_fails(mock_db, admin_token):
    """Uploading a non-CSV file raises HTTP 400."""
    files = {"file": ("malicious.exe", io.BytesIO(b"MZ binary content"), "application/octet-stream")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-data",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 400
        assert "Invalid file format" in res.json()["error"]["message"]


@pytest.mark.asyncio
async def test_csv_import_empty_file_fails(mock_db, admin_token):
    """Uploading an empty file raises HTTP 400."""
    files = {"file": ("empty.csv", io.BytesIO(b""), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-data",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 400
        assert "empty" in res.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_csv_import_forbidden_for_employee(mock_db, employee_token):
    """Non-company_admin role is rejected with HTTP 403 on CSV import."""
    csv_content = "title,content\nDoc,Text\n"
    files = {"file": ("doc.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-data",
            files=files,
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert res.status_code == 403
        assert len(mock_db.records_collection.records) == 0


@pytest.mark.asyncio
async def test_endpoint_accessible_via_v1_and_api_v1(mock_db, admin_token):
    """Both /api/v1/company/data/insert and /v1/company/data/insert routes are accessible."""
    payload = {"title": "Dual Route Test", "content": "Route verification content"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res1 = await client.post(
            "/api/v1/company/data/insert",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res1.status_code == 200

        res2 = await client.post(
            "/v1/company/data/insert",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res2.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/v1/company/import-csv-auto Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_csv_auto_keyword_detection(mock_db, admin_token):
    """Auto-detects title (Document Title) and content (Body Content) keywords and inserts with tenant stamp."""
    csv_content = (
        "Document Title,Body Content,Author\n"
        "Security Guidelines,Must use complex passwords and 2FA.,Security Team\n"
        "Remote Work Policy,WFH requires manager approval.,HR Team\n"
    )
    files = {"file": ("auto.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-auto",
            files=files,
            data={"title_column": "auto-detect", "content_column": "auto-detect"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["total_records"] == 2
        assert data["title_column"] == "Document Title"
        assert data["content_column"] == "Body Content"
        assert data["success"] is True

        # Verify bulk insert in MongoDB with company_id isolation
        assert len(mock_db.records_collection.records) == 2
        for rec in mock_db.records_collection.records:
            assert rec["company_id"] == "acme_corp_999"
            assert rec["created_at"] is not None
            assert len(rec["title"]) > 0
            assert len(rec["content"]) > 0

        assert mock_db.records_collection.records[0]["title"] == "Security Guidelines"
        assert mock_db.records_collection.records[0]["content"] == "Must use complex passwords and 2FA."


@pytest.mark.asyncio
async def test_import_csv_auto_fallback_first_and_second_columns(mock_db, admin_token):
    """When no keyword matches, falls back to first column as title and second column as content."""
    csv_content = (
        "ItemKey,ValueDetail\n"
        "K1,V1\n"
        "K2,V2\n"
    )
    files = {"file": ("fallback.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-auto",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["total_records"] == 2
        assert data["title_column"] == "ItemKey"
        assert data["content_column"] == "ValueDetail"


@pytest.mark.asyncio
async def test_import_csv_auto_invalid_column_raises_400(mock_db, admin_token):
    """Specifying a non-existent column name raises HTTP 400."""
    csv_content = "Title,Content\nT1,C1\n"
    files = {"file": ("test.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-auto",
            files=files,
            data={"title_column": "NonExistentColumn"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 400
        assert "NonExistentColumn" in res.json()["detail"]


@pytest.mark.asyncio
async def test_import_csv_auto_rbac_forbidden_for_employee(mock_db, employee_token):
    """Employee token is rejected with HTTP 403 ForbiddenRole."""
    csv_content = "Title,Content\nT1,C1\n"
    files = {"file": ("test.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/company/import-csv-auto",
            files=files,
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert res.status_code == 403
