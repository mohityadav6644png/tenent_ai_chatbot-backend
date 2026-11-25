"""
Integration tests for cross-file chat questions, multi-part answers, and refusal behavior.
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

TENANT_A = "chat-cross-file-tenant-a"
ADMIN_A = "chat_user_admin_a"


@pytest.fixture
async def setup_chat_cross_files():
    await tenants_collection.update_one(
        {"_id": TENANT_A},
        {"$set": {"name": "BrightPath High", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    await users_collection.update_one(
        {"_id": ADMIN_A},
        {"$set": {"email": "admin@brightpath.edu", "role": "company_admin", "company_id": TENANT_A, "is_active": True}},
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

    # Seed files: Exam marks and Students
    res1 = await company_files_collection.insert_one({
        "company_id": TENANT_A,
        "name": "Exam Marks",
        "slug": "exam_marks",
        "status": "ready",
        "row_count": 4,
        "columns": [
            {"key": "student_id", "type": "text", "hidden": False},
            {"key": "subject", "type": "text", "hidden": False},
            {"key": "marks_obtained", "type": "number", "hidden": False},
        ],
    })
    res2 = await company_files_collection.insert_one({
        "company_id": TENANT_A,
        "name": "Students",
        "slug": "students",
        "status": "ready",
        "row_count": 2,
        "columns": [
            {"key": "student_id", "type": "text", "hidden": False},
            {"key": "name", "type": "text", "hidden": False},
            {"key": "fee_status", "type": "text", "hidden": False},
            {"key": "section", "type": "text", "hidden": False},
            {"key": "secret_ssn", "type": "text", "hidden": True},
        ],
    })
    left_id = str(res1.inserted_id)
    right_id = str(res2.inserted_id)

    # Seed business data rows
    rows_writer = business_database_writer["company_rows"]
    await rows_writer.insert_many([
        {"company_id": TENANT_A, "file_id": left_id, "data": {"student_id": "S1", "subject": "Maths", "marks_obtained": 80}},
        {"company_id": TENANT_A, "file_id": left_id, "data": {"student_id": "S1", "subject": "English", "marks_obtained": 70}},
        {"company_id": TENANT_A, "file_id": left_id, "data": {"student_id": "S2", "subject": "Maths", "marks_obtained": 60}},
        {"company_id": TENANT_A, "file_id": left_id, "data": {"student_id": "S2", "subject": "English", "marks_obtained": 35}},
        {"company_id": TENANT_A, "file_id": right_id, "data": {"student_id": "S1", "name": "Aarav", "fee_status": "Paid", "section": "A", "secret_ssn": "SECRET-1"}},
        {"company_id": TENANT_A, "file_id": right_id, "data": {"student_id": "S2", "name": "Tara Mehta", "fee_status": "Pending", "section": "B", "secret_ssn": "SECRET-2"}},
    ])

    yield {
        "session": raw_token,
        "csrf": csrf_token,
        "left_id": left_id,
        "right_id": right_id,
    }

    # Teardown
    await company_files_collection.delete_many({"company_id": TENANT_A})
    await file_connections_collection.delete_many({"company_id": TENANT_A})
    await business_database_writer["company_rows"].delete_many({"company_id": TENANT_A})
    await sessions_collection.delete_many({"user_id": ADMIN_A})


@pytest.mark.asyncio
async def test_cross_file_chat_full_flow(setup_chat_cross_files):
    from app.container import container
    from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator

    orig_gen = container.query_service.generator
    orig_md = container.query_service.multidoc_service
    orig_fu = container.query_service.followup_service

    container.query_service.generator = FakeSQLGenerator()
    container.query_service.multidoc_service = None
    container.query_service.followup_service = None
    try:
        fixture = setup_chat_cross_files
        cookies = {"session": fixture["session"], "csrf_token": fixture["csrf"]}
        headers = {"X-CSRF-Token": fixture["csrf"]}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
            # 1. Negative check before connection: question needing both files is refused with "no_connection"
            refuse_res = await client.post(
                "/v1/chat/query",
                json={"question": "Which teachers taught the students who failed English?"},
                headers=headers,
            )
            assert refuse_res.status_code == 200
            ref_json = refuse_res.json()
            assert ref_json["status"] == "refused"
            assert ref_json["refusal_reason"] == "no_connection"
            assert "These files aren't connected yet" in ref_json["answer"]["detail"]

            # 2. Create and enrich connection
            create_res = await client.post(
                "/v1/company/connections",
                json={
                    "left_file_id": fixture["left_id"],
                    "left_key": "student_id",
                    "right_file_id": fixture["right_id"],
                    "right_key": "student_id",
                    "name": "Exam Marks -> Students",
                },
                headers=headers,
            )
            assert create_res.status_code == 201
            conn_id = create_res.json()["id"]

            worker = container.enrichment_worker
            enrich_ok = await worker.run_enrichment(TENANT_A, conn_id)
            assert enrich_ok is True

            # 3. Verify catalog reflects connection and alias fields
            catalog = await container.catalog_repo.get_catalog(TENANT_A)
            assert len(catalog.get("connections", [])) >= 1
            exam_file_cat = next((f for f in catalog["files"] if f["slug"] == "exam_marks"), None)
            assert exam_file_cat is not None
            assert len(exam_file_cat["connections"]) >= 1
            cat_keys = [c["key"] for c in exam_file_cat["columns"]]
            assert "students.name" in cat_keys
            assert "students.fee_status" in cat_keys
            assert "students.secret_ssn" not in cat_keys  # Hidden column must NOT be present!

            # 4. Ask cross-file question: "What is the average marks by fee status?"
            chat_res = await client.post(
                "/v1/chat/query",
                json={"question": "What is the average marks by fee status?"},
                headers=headers,
            )
            assert chat_res.status_code == 200
            ans = chat_res.json()
            assert ans["status"] == "success"
            assert ans["meta"]["files_used"] == ["Exam Marks", "Students"]
            assert len(ans["answer"]["rows"]) > 0

            # 5. Ask another cross-file question: "What is the average Maths marks by section?"
            chat_res2 = await client.post(
                "/v1/chat/query",
                json={"question": "What is the average Maths marks by section?"},
                headers=headers,
            )
            assert chat_res2.status_code == 200
            ans2 = chat_res2.json()
            assert ans2["status"] == "success"
            assert ans2["meta"]["files_used"] == ["Exam Marks", "Students"]

            # 6. Ask filtering question: "Which students with Pending fee status failed at least one subject?"
            chat_res3 = await client.post(
                "/v1/chat/query",
                json={"question": "Which students with Pending fee status failed at least one subject?"},
                headers=headers,
            )
            assert chat_res3.status_code == 200
            ans3 = chat_res3.json()
            assert ans3["status"] == "success"
            assert any("Tara Mehta" in str(row) for row in ans3["answer"]["rows"])

            # 7. Multi-part query test: "total marks and total students"
            chat_mp = await client.post(
                "/v1/chat/query",
                json={"question": "total marks and total students"},
                headers=headers,
            )
            assert chat_mp.status_code == 200
            ans_mp = chat_mp.json()
            assert ans_mp["status"] == "success"
            assert ans_mp["answer"]["parts"] is not None
            assert len(ans_mp["answer"]["parts"]) == 2
    finally:
        container.query_service.generator = orig_gen
        container.query_service.multidoc_service = orig_md
        container.query_service.followup_service = orig_fu
