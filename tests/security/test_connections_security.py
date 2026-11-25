"""
Security tests for file connections: tenant isolation, validator rejection, hidden columns, and join-free execution.
"""

from datetime import datetime, timedelta, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.exceptions import SqlValidationFailedError
from app.core.security import generate_csrf_token, generate_opaque_token, hash_token
from app.infrastructure.db.mongodb import (
    company_files_collection,
    file_connections_collection,
    business_database_writer,
    business_database_reader,
    sessions_collection,
    tenants_collection,
    users_collection,
)
from app.modules.sql_guard.validator import validate_and_format_sql

TENANT_A = "sec-tenant-a-1111"
TENANT_B = "sec-tenant-b-2222"
ADMIN_A = "sec_admin_a"
ADMIN_B = "sec_admin_b"


@pytest.fixture
async def setup_security_fixtures():
    # Setup tenants A and B
    for t_id, name in [(TENANT_A, "Tenant A"), (TENANT_B, "Tenant B")]:
        await tenants_collection.update_one(
            {"_id": t_id},
            {"$set": {"name": name, "schema_version": 1, "status": "active"}},
            upsert=True,
        )

    # Setup admin users
    tokens = {}
    for u_id, t_id in [(ADMIN_A, TENANT_A), (ADMIN_B, TENANT_B)]:
        await users_collection.update_one(
            {"_id": u_id},
            {"$set": {"email": f"{u_id}@test.com", "role": "company_admin", "company_id": t_id, "is_active": True}},
            upsert=True,
        )
        raw_token = generate_opaque_token()
        token_hash = hash_token(raw_token)
        csrf_token = generate_csrf_token()
        now = datetime.now(timezone.utc)
        await sessions_collection.insert_one({
            "user_id": u_id,
            "token_hash": token_hash,
            "expires_at": now + timedelta(hours=2),
            "ip": "127.0.0.1",
            "user_agent": "pytest",
            "created_at": now,
        })
        tokens[u_id] = {"session": raw_token, "csrf": csrf_token}

    yield tokens

    # Teardown
    for t_id in [TENANT_A, TENANT_B]:
        await company_files_collection.delete_many({"company_id": t_id})
        await file_connections_collection.delete_many({"company_id": t_id})
        await business_database_writer["company_rows"].delete_many({"company_id": t_id})
    await sessions_collection.delete_many({"user_id": {"$in": [ADMIN_A, ADMIN_B]}})


@pytest.mark.asyncio
async def test_tenant_b_cannot_access_tenant_a_connections_or_files(setup_security_fixtures):
    tokens = setup_security_fixtures
    auth_b = tokens[ADMIN_B]
    cookies_b = {"session": auth_b["session"], "csrf_token": auth_b["csrf"]}
    headers_b = {"X-CSRF-Token": auth_b["csrf"]}

    # Seed file for Tenant A
    res_a = await company_files_collection.insert_one({
        "company_id": TENANT_A,
        "name": "Secret A Data",
        "slug": "secret_a_data",
        "status": "ready",
        "columns": [{"key": "id", "type": "text", "hidden": False}],
    })
    file_a_id = str(res_a.inserted_id)

    # Seed file for Tenant B
    res_b = await company_files_collection.insert_one({
        "company_id": TENANT_B,
        "name": "Data B",
        "slug": "data_b",
        "status": "ready",
        "columns": [{"key": "id", "type": "text", "hidden": False}],
    })
    file_b_id = str(res_b.inserted_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies_b) as client:
        # Tenant B attempting to create connection referencing Tenant A's file must return 404
        cross_res = await client.post(
            "/v1/company/connections",
            json={
                "left_file_id": file_b_id,
                "left_key": "id",
                "right_file_id": file_a_id,  # Belongs to Tenant A!
                "right_key": "id",
            },
            headers=headers_b,
        )
        assert cross_res.status_code == 404


@pytest.mark.asyncio
async def test_validator_rejects_forbidden_join_operators():
    # $lookup is strictly forbidden in AI pipelines
    spec_lookup = {
        "collection": "orders",
        "pipeline": [
            {"$lookup": {"from": "customers", "localField": "customer_id", "foreignField": "id", "as": "cust"}},
            {"$project": {"_id": 0, "order_id": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError, match="Stage '.*' is not in allowed stages"):
        validate_and_format_sql(spec_lookup)

    spec_union = {
        "collection": "orders",
        "pipeline": [
            {"$unionWith": {"coll": "invoices"}},
            {"$project": {"_id": 0, "order_id": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError, match="Stage '.*' is not in allowed stages"):
        validate_and_format_sql(spec_union)


@pytest.mark.asyncio
async def test_validator_rejects_unrequested_or_hidden_join_fields():
    # Connection exists but is NOT requested in "joins"
    active_conns = [{
        "id": "conn-123",
        "alias": "students",
        "left_file_slug": "exam_marks",
        "status": "active",
        "company_id": TENANT_A,
    }]
    cat_fields = {"exam_marks": {"student_id", "marks", "students.name", "students.secret_ssn"}}

    # Query without "joins" referencing $students.name must be rejected
    spec_unrequested = {
        "collection": "exam_marks",
        "pipeline": [
            {"$group": {"_id": "$students.name", "total": {"$sum": "$marks"}}},
            {"$project": {"_id": 0, "student": "$_id", "total": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError, match="unknown or unrequested join"):
        validate_and_format_sql(spec_unrequested, allowed_collections={"exam_marks"}, catalog_fields=cat_fields, connections=active_conns)

    # Query with foreign company join ID must be rejected
    foreign_conns = [{
        "id": "conn-foreign",
        "alias": "students",
        "left_file_slug": "exam_marks",
        "status": "active",
        "company_id": "other-company",
    }]
    spec_with_foreign_join = {
        "collection": "exam_marks",
        "joins": ["conn-foreign"],
        "pipeline": [
            {"$project": {"_id": 0, "student_id": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError, match="does not belong to tenant"):
        validate_and_format_sql(
            spec_with_foreign_join,
            allowed_collections={"exam_marks"},
            catalog_fields=cat_fields,
            connections=foreign_conns,
            company_id=TENANT_A,
        )
