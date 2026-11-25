"""
Integration tests for file connections endpoints and enrichment worker.
"""

from datetime import datetime, timedelta, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.security import generate_csrf_token, generate_opaque_token, hash_token
from app.infrastructure.db.mongodb import (
    company_files_collection,
    file_connections_collection,
    business_database_writer,
    sessions_collection,
    tenants_collection,
    users_collection,
)

TENANT_A = "aaaa1111-1111-1111-1111-111111111111"
ADMIN_A = "user_admin_a"


@pytest.fixture
async def setup_auth_and_files():
    # Setup tenant and user
    await tenants_collection.update_one(
        {"_id": TENANT_A},
        {"$set": {"name": "Tenant A Corp", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    await users_collection.update_one(
        {"_id": ADMIN_A},
        {"$set": {"email": "admin_a@test.com", "role": "company_admin", "company_id": TENANT_A, "is_active": True}},
        upsert=True,
    )

    raw_token = generate_opaque_token()
    token_hash = hash_token(raw_token)
    csrf_token = generate_csrf_token()
    now = datetime.now(timezone.utc)

    await sessions_collection.insert_one({
        "user_id": ADMIN_A,
        "token_hash": token_hash,
        "expires_at": now + timedelta(hours=2),
        "ip": "127.0.0.1",
        "user_agent": "pytest",
        "created_at": now,
    })

    yield {"session": raw_token, "csrf": csrf_token}

    # Teardown
    await company_files_collection.delete_many({"company_id": TENANT_A})
    await file_connections_collection.delete_many({"company_id": TENANT_A})
    await business_database_writer["company_rows"].delete_many({"company_id": TENANT_A})
    await sessions_collection.delete_many({"user_id": ADMIN_A})


@pytest.mark.asyncio
async def test_file_connections_crud_flow(setup_auth_and_files):
    from app.container import container
    auth = setup_auth_and_files
    cookies = {"session": auth["session"], "csrf_token": auth["csrf"]}
    headers = {"X-CSRF-Token": auth["csrf"]}

    # Seed two ready files
    left_file = {
        "company_id": TENANT_A,
        "name": "Exam Marks",
        "slug": "exam_marks",
        "status": "ready",
        "columns": [
            {"key": "student_id", "type": "text", "hidden": False},
            {"key": "marks", "type": "number", "hidden": False},
        ],
    }
    right_file = {
        "company_id": TENANT_A,
        "name": "Students",
        "slug": "students",
        "status": "ready",
        "columns": [
            {"key": "student_id", "type": "text", "hidden": False},
            {"key": "name", "type": "text", "hidden": False},
            {"key": "secret_ssn", "type": "text", "hidden": True},
        ],
    }
    res1 = await company_files_collection.insert_one(left_file)
    res2 = await company_files_collection.insert_one(right_file)
    left_id = str(res1.inserted_id)
    right_id = str(res2.inserted_id)

    # Seed rows
    rows_writer = business_database_writer["company_rows"]
    await rows_writer.insert_many([
        {"company_id": TENANT_A, "file_id": left_id, "data": {"student_id": "S1", "marks": 85}},
        {"company_id": TENANT_A, "file_id": left_id, "data": {"student_id": "S2", "marks": 90}},
        {"company_id": TENANT_A, "file_id": right_id, "data": {"student_id": "S1", "name": "Alice", "secret_ssn": "111"}},
        {"company_id": TENANT_A, "file_id": right_id, "data": {"student_id": "S2", "name": "Bob", "secret_ssn": "222"}},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
        # 1. Preview
        prev_res = await client.post(
            "/v1/company/connections/preview",
            json={
                "left_file_id": left_id,
                "left_key": "student_id",
                "right_file_id": right_id,
                "right_key": "student_id",
            },
            headers=headers,
        )
        assert prev_res.status_code == 200
        prev_data = prev_res.json()
        assert prev_data["ok"] is True
        assert prev_data["right_unique"] is True
        assert prev_data["match_rate"] == 1.0

        # 2. Create Connection
        create_res = await client.post(
            "/v1/company/connections",
            json={
                "left_file_id": left_id,
                "left_key": "student_id",
                "right_file_id": right_id,
                "right_key": "student_id",
                "name": "Marks -> Students",
            },
            headers=headers,
        )
        assert create_res.status_code == 201
        conn_id = create_res.json()["id"]

        # Run enrichment manually
        worker = container.enrichment_worker
        success = await worker.run_enrichment(TENANT_A, conn_id)
        assert success is True

        # Check enrichment result on row
        s1_row = await rows_writer.find_one({"company_id": TENANT_A, "file_id": left_id, "data.student_id": "S1"})
        assert s1_row is not None
        assert "joined" in s1_row
        assert "students" in s1_row["joined"]
        assert s1_row["joined"]["students"]["name"] == "Alice"
        # Hidden column must NEVER be copied
        assert "secret_ssn" not in s1_row["joined"]["students"]

        # 3. List Connections
        list_res = await client.get("/v1/company/connections", headers=headers)
        assert list_res.status_code == 200
        conns = list_res.json()["connections"]
        assert len(conns) >= 1
        assert any(c["id"] == conn_id and c["status"] == "active" for c in conns)

        # 4. Refresh Connection
        ref_res = await client.post(f"/v1/company/connections/{conn_id}/refresh", headers=headers)
        assert ref_res.status_code == 202

        # 5. Delete Connection
        del_res = await client.delete(f"/v1/company/connections/{conn_id}", headers=headers)
        assert del_res.status_code == 204
