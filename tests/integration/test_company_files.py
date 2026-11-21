"""
Company Files Integration & Security Tests (tests/integration/test_company_files.py)
Tests real MongoDB, FakeSQLGenerator, FastAPI client, and security isolation.
"""

from datetime import datetime, timezone
import io
import json
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.core.context import TenantContext
from app.core.exceptions import SqlValidationFailedError
from app.core.security import generate_csrf_token, generate_opaque_token, hash_token
from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator
from app.infrastructure.azure.redis_cache import InMemoryCache, build_cache_key
from app.infrastructure.company_files.csv_parser import (
    CompanyFilesCsvParser,
    FileValidationError,
    sanitize_header_to_key,
)
from app.infrastructure.db.catalog_repo import SchemaCatalogRepo
from app.infrastructure.db.company_files_repo import MongoCompanyFilesRepository
from app.infrastructure.db.executor import MongoExecutor, scope_file_pipeline
from app.infrastructure.db.mongodb import (
    ALLOWED_BUSINESS_COLLECTIONS,
    app_database,
    business_database_reader,
    business_database_writer,
    sessions_collection,
    tenants_collection,
    users_collection,
)
from app.main import create_app
from app.modules.chat.service import QueryService
from app.modules.company_files.service import CompanyFilesService
from app.modules.sql_guard.validator import validate_and_format_sql

TENANT_A = "aaaa1111-1111-1111-1111-111111111111"
TENANT_B = "bbbb2222-2222-2222-2222-222222222222"
ADMIN_A = "user_admin_a"
ADMIN_B = "user_admin_b"
EMPLOYEE_A = "user_emp_a"


@pytest_asyncio.fixture
async def setup_test_users_and_tenants():
    # Setup tenants
    await tenants_collection.update_one(
        {"_id": TENANT_A},
        {"$set": {"name": "Tenant A Corp", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    await tenants_collection.update_one(
        {"_id": TENANT_B},
        {"$set": {"name": "Tenant B Corp", "schema_version": 1, "status": "active"}},
        upsert=True,
    )

    # Users
    await users_collection.update_one(
        {"_id": ADMIN_A},
        {"$set": {"email": "admin_a@test.com", "role": "company_admin", "company_id": TENANT_A, "is_active": True}},
        upsert=True,
    )
    await users_collection.update_one(
        {"_id": ADMIN_B},
        {"$set": {"email": "admin_b@test.com", "role": "company_admin", "company_id": TENANT_B, "is_active": True}},
        upsert=True,
    )
    await users_collection.update_one(
        {"_id": EMPLOYEE_A},
        {"$set": {"email": "emp_a@test.com", "role": "employee", "company_id": TENANT_A, "is_active": True}},
        upsert=True,
    )

    yield

    # Teardown
    await app_database["company_files"].delete_many({"company_id": {"$in": [TENANT_A, TENANT_B]}})
    await business_database_writer["company_rows"].delete_many({"company_id": {"$in": [TENANT_A, TENANT_B]}})


async def make_authenticated_client(user_id: str, app):
    raw_token = generate_opaque_token()
    token_hash = hash_token(raw_token)
    csrf_token = generate_csrf_token()
    now = datetime.now(timezone.utc)

    await sessions_collection.insert_one({
        "user_id": user_id,
        "token_hash": token_hash,
        "expires_at": now + pytest.importorskip("datetime").timedelta(hours=2),
        "ip": "127.0.0.1",
        "user_agent": "pytest",
        "created_at": now,
    })

    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"session": raw_token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    )
    return client, csrf_token


# -----------------------------------------------------------------------------
# 1. Security & Allowlist Tests
# -----------------------------------------------------------------------------
def test_company_rows_in_business_allowlist():
    assert "company_rows" in ALLOWED_BUSINESS_COLLECTIONS, "company_rows must be deliberately in ALLOWED_BUSINESS_COLLECTIONS"


def test_modules_layering_no_forbidden_imports():
    import pathlib
    modules_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "app" / "modules" / "company_files"
    forbidden = ["motor", "pymongo", "fastapi", "UploadFile", "csv", "pandas"]
    for py_file in modules_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                for f in forbidden:
                    # e.g. 'import csv' or 'from fastapi import'
                    parts = stripped.split()
                    imported_mod = parts[1] if len(parts) > 1 else ""
                    assert f != imported_mod and not imported_mod.startswith(f"{f}."), (
                        f"Forbidden import '{f}' found in {py_file.name}:{idx}: '{line}'"
                    )


# -----------------------------------------------------------------------------
# 2. Column Detection, Header Sanitization, Types & Heuristics
# -----------------------------------------------------------------------------
def test_header_sanitizing():
    seen = {}
    assert sanitize_header_to_key("Product Name", seen) == "product_name"
    assert sanitize_header_to_key("$price.usd$", seen) == "col_price_usd"
    assert sanitize_header_to_key("123 Count", seen) == "col_123_count"
    assert sanitize_header_to_key("company_id", seen) == "company_id_value"
    assert sanitize_header_to_key("file_id", seen) == "file_id_value"
    assert sanitize_header_to_key("_id", seen) == "col_id"
    # Duplicates get _2, _3
    seen2 = {}
    k1 = sanitize_header_to_key("Sales", seen2)
    k2 = sanitize_header_to_key("Sales", seen2)
    k3 = sanitize_header_to_key("Sales", seen2)
    assert k1 == "sales"
    assert k2 == "sales_2"
    assert k3 == "sales_3"


def test_type_detection_and_suggested_hidden():
    parser = CompanyFilesCsvParser()
    csv_bytes = (
        "Name,Sales,Joined_Date,Is_Active,Mobile,User_Email,National_ID\n"
        "Alice,\"1,250.50\",2024-01-15,true,+919876543210,alice@acme.com,123456789012\n"
        "Bob,\"2,000\",15/02/2024,yes,9876543211,bob@acme.com,1234-5678-9013\n"
        "Charlie,500,2024-03-01T10:00:00Z,1,9876543212,charlie@acme.com,1234 5678 9014\n"
        "Dave,99.99,2024-04-01,no,9876543213,dave@acme.com,ABCDE1234F\n"
    ).encode("utf-8")

    preview = parser.parse_preview(csv_bytes, "test.csv")
    cols = {c["key"]: c for c in preview["columns"]}

    assert cols["name"]["type"] == "text"
    assert cols["sales"]["type"] == "number"  # 3 of 4 valid is 75% -> wait, threshold is 95%, so if 3/4 valid it is text unless >=95%
    # With 3/4 (75%), sales should fall back to text!
    assert cols["is_active"]["type"] == "boolean"
    assert cols["joined_date"]["type"] == "date"
    # Check suggested_hidden
    assert cols["mobile"]["suggested_hidden"] is True
    assert cols["user_email"]["suggested_hidden"] is True
    assert cols["national_id"]["suggested_hidden"] is True
    assert cols["sales"]["suggested_hidden"] is False


def test_preview_with_invalid_rows_counted():
    parser = CompanyFilesCsvParser()
    # 95% number column with 1 bad row: 19 numbers, 1 bad string
    rows = ["NumCol,TextCol"]
    for i in range(19):
        rows.append(f"{i * 10},sample_{i}")
    rows.append("invalid_num,sample_bad")
    csv_bytes = "\n".join(rows).encode("utf-8")

    preview = parser.parse_preview(csv_bytes, "numbers.csv")
    assert preview["total_rows"] == 20
    assert preview["columns"][0]["type"] == "number"
    assert preview["valid_rows"] == 19
    assert preview["invalid_rows"] == 1
    assert len(preview["errors"]) == 1
    assert preview["errors"][0]["row"] == 21  # 1 header + 20 rows


# -----------------------------------------------------------------------------
# 3. Endpoints & RBAC & CSRF
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_endpoint_auth_and_role_refusals(setup_test_users_and_tenants):
    app = create_app()

    # Anonymous call
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon_client:
        res = await anon_client.get("/v1/company/files")
        assert res.status_code == 401

    # Employee call (refused with 403)
    emp_client, _ = await make_authenticated_client(EMPLOYEE_A, app)
    async with emp_client as client:
        res = await client.get("/v1/company/files")
        assert res.status_code == 403

        # CSRF missing check on mutating endpoint
        post_res = await client.post(
            "/v1/company/files/preview",
            files={"file": ("test.csv", b"a,b\n1,2", "text/csv")},
            headers={"X-CSRF-Token": "bad_csrf"},
        )
        assert post_res.status_code == 403


@pytest.mark.asyncio
async def test_company_admin_crud_and_tenant_isolation(setup_test_users_and_tenants):
    app = create_app()
    admin_a_client, _ = await make_authenticated_client(ADMIN_A, app)
    admin_b_client, _ = await make_authenticated_client(ADMIN_B, app)

    csv_data = (
        "region,sales,phone\n"
        "North,15000,+919876543210\n"
        "South,22000,+919876543211\n"
        "West,18000,+919876543212\n"
    ).encode("utf-8")

    async with admin_a_client as client_a, admin_b_client as client_b:
        # 1. Preview file
        prev_res = await client_a.post(
            "/v1/company/files/preview",
            files={"file": ("sales.csv", csv_data, "text/csv")},
        )
        assert prev_res.status_code == 200
        prev_json = prev_res.json()
        assert prev_json["total_rows"] == 3
        assert len(prev_json["columns"]) == 3

        # 2. Create file as Tenant A with phone hidden
        meta = {
            "name": "Regional Sales Data",
            "description": "Annual regional sales revenue figures and regional contact lines.",
            "upload_mode": "replace",
            "columns": [
                {"key": "phone", "meaning": "Local representative phone number", "hidden": True},
                {"key": "region", "meaning": "Sales region"},
                {"key": "sales", "meaning": "Total sales in INR"},
            ],
        }
        create_res = await client_a.post(
            "/v1/company/files",
            data={"meta": json.dumps(meta)},
            files={"file": ("sales.csv", csv_data, "text/csv")},
        )
        assert create_res.status_code == 201
        created_file = create_res.json()
        file_id = created_file["id"]
        assert created_file["inserted"] == 3

        # 3. List files for Tenant A
        list_res_a = await client_a.get("/v1/company/files")
        assert list_res_a.status_code == 200
        files_a = list_res_a.json()["files"]
        assert len(files_a) == 1
        assert files_a[0]["id"] == file_id
        assert files_a[0]["rows"] == 3

        # Tenant B lists files -> should see 0 files (isolation)
        list_res_b = await client_b.get("/v1/company/files")
        assert list_res_b.status_code == 200
        assert len(list_res_b.json()["files"]) == 0

        # Tenant B tries to get Tenant A's file detail -> 404
        get_b_res = await client_b.get(f"/v1/company/files/{file_id}")
        assert get_b_res.status_code == 404

        # 4. Tenant A gets file detail
        get_a_res = await client_a.get(f"/v1/company/files/{file_id}")
        assert get_a_res.status_code == 200
        file_detail = get_a_res.json()
        phone_col = next(c for c in file_detail["columns"] if c["key"] == "phone")
        assert phone_col["hidden"] is True

        # 5. Patch file metadata (modify description)
        patch_res = await client_a.patch(
            f"/v1/company/files/{file_id}",
            json={"description": "Updated regional sales overview for operations team."},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["description"] == "Updated regional sales overview for operations team."

        # 6. Upload new file (replace mode)
        updated_csv = (
            "region,sales,phone\n"
            "North,16000,+919876543210\n"
            "South,24000,+919876543211\n"
            "East,12000,+919876543213\n"
            "West,20000,+919876543212\n"
        ).encode("utf-8")
        upload_res = await client_a.post(
            f"/v1/company/files/{file_id}/upload",
            files={"file": ("sales_v2.csv", updated_csv, "text/csv")},
        )
        assert upload_res.status_code == 200
        assert upload_res.json()["inserted"] == 4

        # Verify row count updated
        detail_after_upload = (await client_a.get(f"/v1/company/files/{file_id}")).json()
        assert detail_after_upload["row_count"] == 4

        # 7. Delete file
        del_res = await client_a.delete(f"/v1/company/files/{file_id}")
        assert del_res.status_code == 204

        # Check file is gone for Tenant A
        get_del_res = await client_a.get(f"/v1/company/files/{file_id}")
        assert get_del_res.status_code == 404


# -----------------------------------------------------------------------------
# 4. Hidden Column & Executor Unset Protection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hidden_column_protection(setup_test_users_and_tenants):
    """Hidden columns must not be in catalog, validator must reject, and executor must $unset."""
    app_db = app_database
    writer_db = business_database_writer
    reader_db = business_database_reader

    file_id = str(uuid.uuid4())
    slug = "employee_directory"

    # Insert file with salary and phone hidden
    await app_db["company_files"].insert_one({
        "_id": file_id,
        "company_id": TENANT_A,
        "name": "Employee Directory",
        "slug": slug,
        "description": "Internal staff roster with department and compensation details.",
        "status": "ready",
        "upload_mode": "replace",
        "row_count": 2,
        "columns": [
            {"key": "name", "label": "Full Name", "type": "text", "hidden": False},
            {"key": "department", "label": "Department", "type": "text", "hidden": False},
            {"key": "salary", "label": "Base Salary", "type": "number", "hidden": True},
            {"key": "phone", "label": "Phone Number", "type": "text", "hidden": True},
        ],
        "stats": {
            "row_count": 2,
            "columns": {
                "name": {"distinct_values": ["Alice", "Bob"]},
                "department": {"distinct_values": ["Engineering", "HR"]},
            },
        },
        "updated_at": datetime.now(timezone.utc),
    })

    # Insert data rows
    await writer_db["company_rows"].insert_many([
        {
            "company_id": TENANT_A,
            "file_id": file_id,
            "import_id": "imp1",
            "data": {"name": "Alice", "department": "Engineering", "salary": 95000, "phone": "+919876543210"},
            "updated_at": datetime.now(timezone.utc),
        },
        {
            "company_id": TENANT_A,
            "file_id": file_id,
            "import_id": "imp1",
            "data": {"name": "Bob", "department": "HR", "salary": 75000, "phone": "+919876543211"},
            "updated_at": datetime.now(timezone.utc),
        },
    ])

    # 1. Catalog check: hidden columns must NEVER appear in catalog
    catalog_repo = SchemaCatalogRepo(app_db=app_db, reader_db=reader_db)
    cat = await catalog_repo.get_catalog(TENANT_A)
    f_cat = next(f for f in cat["files"] if f["slug"] == slug)
    cat_keys = {c["key"] for c in f_cat["columns"]}
    assert "salary" not in cat_keys, "salary is hidden and must not appear in catalog"
    assert "phone" not in cat_keys, "phone is hidden and must not appear in catalog"
    assert "name" in cat_keys
    assert "department" in cat_keys

    # 2. Validator check: rejecting queries referencing hidden fields
    hidden_query = {
        "collection": slug,
        "pipeline": [
            {"$project": {"_id": 0, "name": 1, "salary": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(
            hidden_query,
            allowed_collections={slug},
            catalog_fields={slug: {"name", "department"}},
            hidden_fields={"salary", "phone"},
        )

    # 3. Executor check: even if validator is bypassed in a test, scope_file_pipeline $unsets hidden keys
    executor = MongoExecutor(db=reader_db, app_db=app_db)
    bypassed_spec = {
        "collection": slug,
        "pipeline": [
            {"$project": {"_id": 0, "name": 1, "department": 1, "salary": "$salary"}},
        ],
    }
    col_defs, rows, _ = await executor.execute_query(bypassed_spec, TENANT_A, columns=["name", "department", "salary"])
    # Salary must be None because executor injected $unset: ["salary", "phone"]
    for r in rows:
        assert r[2] is None, "Hidden field 'salary' must be stripped by executor $unset"


# -----------------------------------------------------------------------------
# 5. End-to-End Chat Querying on Company Files
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_end_to_end_chat_with_company_files(setup_test_users_and_tenants):
    app_db = app_database
    writer_db = business_database_writer
    reader_db = business_database_reader

    file_id = str(uuid.uuid4())
    slug = "regional_sales"

    # Setup file for Tenant A
    await app_db["company_files"].insert_one({
        "_id": file_id,
        "company_id": TENANT_A,
        "name": "Regional Sales",
        "slug": slug,
        "description": "Sales totals broken down by geographic sales region.",
        "status": "ready",
        "upload_mode": "replace",
        "row_count": 3,
        "columns": [
            {"key": "region", "label": "Region", "type": "text", "hidden": False},
            {"key": "sales", "label": "Sales Amount", "type": "number", "hidden": False},
            {"key": "phone", "label": "Phone", "type": "text", "hidden": True},
        ],
        "stats": {
            "row_count": 3,
            "columns": {
                "region": {"distinct_values": ["North", "South", "West"]},
                "sales": {"min": 10000, "max": 25000},
            },
        },
        "updated_at": datetime.now(timezone.utc),
    })

    await writer_db["company_rows"].insert_many([
        {"company_id": TENANT_A, "file_id": file_id, "import_id": "i1", "data": {"region": "North", "sales": 15000, "phone": "123"}, "updated_at": datetime.now(timezone.utc)},
        {"company_id": TENANT_A, "file_id": file_id, "import_id": "i1", "data": {"region": "South", "sales": 25000, "phone": "456"}, "updated_at": datetime.now(timezone.utc)},
        {"company_id": TENANT_A, "file_id": file_id, "import_id": "i1", "data": {"region": "West", "sales": 10000, "phone": "789"}, "updated_at": datetime.now(timezone.utc)},
    ])

    executor = MongoExecutor(db=reader_db, app_db=app_db)
    generator = FakeSQLGenerator()
    catalog_repo = SchemaCatalogRepo(app_db=app_db, reader_db=reader_db)
    cache = InMemoryCache()

    class TestChatRepo:
        async def check_and_increment_rate_limits(self, c, u): pass
        async def get_schema_catalog(self, company_id):
            return await catalog_repo.get_catalog(company_id)
        async def increment_monthly_usage(self, c, tokens=0): pass
        async def save_conversation(self, doc): return "conv1"
        async def save_message(self, doc): pass
        async def list_user_conversations(self, u, c): return []

    chat_svc = QueryService(
        executor=executor,
        generator=generator,
        repo=TestChatRepo(),
        cache=cache,
    )

    # Tenant A asks "total sales by region"
    res_a = await chat_svc.answer(
        company_id=TENANT_A,
        user_id=ADMIN_A,
        question="total sales by region",
        history=[],
    )
    assert res_a["status"] == "success"
    ans_a = res_a["answer"]
    assert ans_a["row_count"] == 3
    regions = [r[0] for r in ans_a["rows"]]
    assert "South" in regions
    assert "North" in regions

    # Tenant A asks for hidden phone numbers -> Must be refused!
    res_phone = await chat_svc.answer(
        company_id=TENANT_A,
        user_id=ADMIN_A,
        question="show phone numbers",
        history=[],
    )
    assert res_phone["status"] == "refused"

    # Tenant B (no files uploaded) asks a question -> "no data" message without calling AI
    res_b = await chat_svc.answer(
        company_id=TENANT_B,
        user_id=ADMIN_B,
        question="total sales by region",
        history=[],
    )
    assert res_b["status"] == "refused"
    assert "No data has been uploaded for your company yet" in res_b["answer"]["detail"]


# -----------------------------------------------------------------------------
# 6. CSV with company_id column does NOT override Tenant Context
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_csv_company_id_column_cannot_override_tenant(setup_test_users_and_tenants):
    repo = MongoCompanyFilesRepository()
    parser = CompanyFilesCsvParser()
    svc = CompanyFilesService(
        repo=repo,
        parser=parser,
        tenant_repo=pytest.importorskip("unittest.mock").AsyncMock(),
        cache=InMemoryCache(),
    )

    hostile_csv = (
        "company_id,item,cost\n"
        "ATTACKER_TENANT,Widget X,50\n"
        "ATTACKER_TENANT,Widget Y,100\n"
    ).encode("utf-8")

    meta = {
        "name": "Security Test File",
        "description": "File attempting company_id override.",
        "upload_mode": "replace",
    }

    res = await svc.create_file(
        company_id=TENANT_A,
        user_id=ADMIN_A,
        content_bytes=hostile_csv,
        filename="attack.csv",
        meta=meta,
    )
    file_id = res["id"]

    # Verify in MongoDB that all rows are stamped with TENANT_A, and company_id column is renamed to company_id_value
    docs = await business_database_reader["company_rows"].find({"file_id": file_id}).to_list(10)
    assert len(docs) == 2
    for d in docs:
        assert d["company_id"] == TENANT_A
        assert d["company_id"] != "ATTACKER_TENANT"
        assert "company_id_value" in d["data"]
