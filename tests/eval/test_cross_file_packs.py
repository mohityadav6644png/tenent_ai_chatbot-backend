"""
Cross-file evaluation suite testing BrightPath, Acme Traders, and Zenith Sales packs.
Asserts positive answers with cross-file fields and negative questions with no_connection refusals.
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
from app.container import container
from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator


@pytest.fixture
async def setup_eval_packs():
    company_id = "eval-packs-tenant"
    admin_id = "eval_packs_admin"

    await tenants_collection.update_one(
        {"_id": company_id},
        {"$set": {"name": "Eval Corp", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    await users_collection.update_one(
        {"_id": admin_id},
        {"$set": {"email": "admin@evalcorp.com", "role": "company_admin", "company_id": company_id, "is_active": True}},
        upsert=True,
    )

    raw_token = generate_opaque_token()
    token_hash = hash_token(raw_token)
    csrf_token = generate_csrf_token()
    now = datetime.now(timezone.utc)

    await sessions_collection.insert_one({
        "user_id": admin_id,
        "token_hash": token_hash,
        "expires_at": now + timedelta(hours=2),
        "ip": "127.0.0.1",
        "user_agent": "eval_test",
        "created_at": now,
    })

    yield {"session": raw_token, "csrf": csrf_token, "company_id": company_id}

    await company_files_collection.delete_many({"company_id": company_id})
    await file_connections_collection.delete_many({"company_id": company_id})
    await business_database_writer["company_rows"].delete_many({"company_id": company_id})
    await sessions_collection.delete_many({"user_id": admin_id})
    await users_collection.delete_one({"_id": admin_id})
    await tenants_collection.delete_one({"_id": company_id})


@pytest.mark.asyncio
async def test_eval_brightpath_pack(setup_eval_packs):
    fixture = setup_eval_packs
    company_id = fixture["company_id"]
    cookies = {"session": fixture["session"], "csrf_token": fixture["csrf"]}
    headers = {"X-CSRF-Token": fixture["csrf"]}

    # Seed Exam Marks, Students, and Teachers
    f1 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Exam Marks", "slug": "exam_marks", "status": "ready",
        "columns": [{"key": "student_id", "type": "text"}, {"key": "marks_obtained", "type": "number"}, {"key": "subject", "type": "text"}]
    })
    f2 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Students", "slug": "students", "status": "ready",
        "columns": [{"key": "student_id", "type": "text"}, {"key": "name", "type": "text"}, {"key": "fee_status", "type": "text"}, {"key": "section", "type": "text"}]
    })
    await company_files_collection.insert_one({
        "company_id": company_id, "name": "Teachers", "slug": "teachers", "status": "ready",
        "columns": [{"key": "teacher_id", "type": "text"}, {"key": "name", "type": "text"}, {"key": "subject", "type": "text"}]
    })

    rows_w = business_database_writer["company_rows"]
    await rows_w.insert_many([
        {"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"student_id": "S1", "marks_obtained": 85, "subject": "Maths"}},
        {"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"student_id": "S2", "marks_obtained": 45, "subject": "Maths"}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"student_id": "S1", "name": "Aarav", "fee_status": "Paid", "section": "A"}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"student_id": "S2", "name": "Tara", "fee_status": "Pending", "section": "B"}},
    ])

    orig_gen, orig_md, orig_fu = container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service
    container.query_service.generator = FakeSQLGenerator()
    container.query_service.multidoc_service = None
    container.query_service.followup_service = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
            # Create connection between Exam Marks and Students
            c_res = await client.post("/v1/company/connections", json={
                "left_file_id": str(f1.inserted_id), "left_key": "student_id",
                "right_file_id": str(f2.inserted_id), "right_key": "student_id", "name": "Marks->Students"
            }, headers=headers)
            assert c_res.status_code == 201
            await container.enrichment_worker.run_enrichment(company_id, c_res.json()["id"])

            # Positive 1
            res1 = await client.post("/v1/chat/query", json={"question": "What is the average marks by fee status?"}, headers=headers)
            assert res1.status_code == 200 and res1.json()["status"] == "success"
            assert res1.json()["meta"]["files_used"] == ["Exam Marks", "Students"]

            # Positive 2
            res2 = await client.post("/v1/chat/query", json={"question": "What is the average Maths marks by section?"}, headers=headers)
            assert res2.status_code == 200 and res2.json()["status"] == "success"

            # Negative (unconnected Teachers file)
            res_neg = await client.post("/v1/chat/query", json={"question": "Which teachers taught the students who failed English?"}, headers=headers)
            assert res_neg.status_code == 200 and res_neg.json()["status"] == "refused"
            assert res_neg.json()["refusal_reason"] == "no_connection"
    finally:
        container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service = orig_gen, orig_md, orig_fu


@pytest.mark.asyncio
async def test_eval_acme_traders_pack(setup_eval_packs):
    fixture = setup_eval_packs
    company_id = fixture["company_id"]
    cookies = {"session": fixture["session"], "csrf_token": fixture["csrf"]}
    headers = {"X-CSRF-Token": fixture["csrf"]}

    # Seed Orders, Customers, Suppliers
    f1 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Orders", "slug": "orders", "status": "ready",
        "columns": [{"key": "order_id", "type": "text"}, {"key": "customer_id", "type": "text"}, {"key": "total", "type": "number"}]
    })
    f2 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Customers", "slug": "customers", "status": "ready",
        "columns": [{"key": "id", "type": "text"}, {"key": "tier", "type": "text"}, {"key": "city", "type": "text"}]
    })

    rows_w = business_database_writer["company_rows"]
    await rows_w.insert_many([
        {"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"order_id": "O1", "customer_id": "C1", "total": 500}},
        {"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"order_id": "O2", "customer_id": "C2", "total": 300}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"id": "C1", "tier": "Gold", "city": "Mumbai"}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"id": "C2", "tier": "Silver", "city": "Delhi"}},
    ])

    orig_gen, orig_md, orig_fu = container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service
    container.query_service.generator = FakeSQLGenerator()
    container.query_service.multidoc_service = None
    container.query_service.followup_service = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
            c_res = await client.post("/v1/company/connections", json={
                "left_file_id": str(f1.inserted_id), "left_key": "customer_id",
                "right_file_id": str(f2.inserted_id), "right_key": "id", "name": "Orders->Customers"
            }, headers=headers)
            assert c_res.status_code == 201
            await container.enrichment_worker.run_enrichment(company_id, c_res.json()["id"])

            # Positive 1
            res1 = await client.post("/v1/chat/query", json={"question": "What is the total sales by customer tier?"}, headers=headers)
            assert res1.status_code == 200 and res1.json()["status"] == "success"

            # Positive 2
            res2 = await client.post("/v1/chat/query", json={"question": "Show the top 3 cities by sales"}, headers=headers)
            assert res2.status_code == 200 and res2.json()["status"] == "success"

            # Negative
            res_neg = await client.post("/v1/chat/query", json={"question": "Which supplier provided goods for customers?"}, headers=headers)
            assert res_neg.status_code == 200 and res_neg.json()["status"] == "refused"
            assert res_neg.json()["refusal_reason"] == "no_connection"
    finally:
        container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service = orig_gen, orig_md, orig_fu


@pytest.mark.asyncio
async def test_eval_zenith_sales_pack(setup_eval_packs):
    fixture = setup_eval_packs
    company_id = fixture["company_id"]
    cookies = {"session": fixture["session"], "csrf_token": fixture["csrf"]}
    headers = {"X-CSRF-Token": fixture["csrf"]}

    f1 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Deals", "slug": "deals", "status": "ready",
        "columns": [{"key": "deal_id", "type": "text"}, {"key": "sales_rep", "type": "text"}, {"key": "value", "type": "number"}, {"key": "stage", "type": "text"}]
    })
    f2 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Sales Team", "slug": "sales_team", "status": "ready",
        "columns": [{"key": "rep_name", "type": "text"}, {"key": "region", "type": "text"}, {"key": "manager", "type": "text"}]
    })

    rows_w = business_database_writer["company_rows"]
    await rows_w.insert_many([
        {"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"deal_id": "D1", "sales_rep": "Alice", "value": 10000, "stage": "Closed Won"}},
        {"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"deal_id": "D2", "sales_rep": "Bob", "value": 5000, "stage": "Closed Won"}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"rep_name": "Alice", "region": "North", "manager": "Carlos"}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"rep_name": "Bob", "region": "South", "manager": "Carlos"}},
    ])

    orig_gen, orig_md, orig_fu = container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service
    container.query_service.generator = FakeSQLGenerator()
    container.query_service.multidoc_service = None
    container.query_service.followup_service = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
            c_res = await client.post("/v1/company/connections", json={
                "left_file_id": str(f1.inserted_id), "left_key": "sales_rep",
                "right_file_id": str(f2.inserted_id), "right_key": "rep_name", "name": "Deals->Sales Team"
            }, headers=headers)
            assert c_res.status_code == 201
            await container.enrichment_worker.run_enrichment(company_id, c_res.json()["id"])

            # Positive 1
            res1 = await client.post("/v1/chat/query", json={"question": "Show won deal value by manager"}, headers=headers)
            assert res1.status_code == 200 and res1.json()["status"] == "success"

            # Positive 2
            res2 = await client.post("/v1/chat/query", json={"question": "Show won deal value by region"}, headers=headers)
            assert res2.status_code == 200 and res2.json()["status"] == "success"

            # Negative
            res_neg = await client.post("/v1/chat/query", json={"question": "Which managers met their quarterly targets?"}, headers=headers)
            assert res_neg.status_code == 200 and res_neg.json()["status"] == "refused"
            assert res_neg.json()["refusal_reason"] == "no_connection"
    finally:
        container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service = orig_gen, orig_md, orig_fu


@pytest.mark.asyncio
async def test_eval_citycare_clinic_pack(setup_eval_packs):
    fixture = setup_eval_packs
    company_id = fixture["company_id"]
    cookies = {"session": fixture["session"], "csrf_token": fixture["csrf"]}
    headers = {"X-CSRF-Token": fixture["csrf"]}

    f_app = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Appointments", "slug": "appointments", "status": "ready",
        "columns": [
            {"key": "appointment_id", "type": "text"},
            {"key": "doctor", "type": "text"},
            {"key": "patient_name", "type": "text"},
        ]
    })
    f_doc = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Doctors", "slug": "doctors", "status": "ready",
        "columns": [
            {"key": "doctor_id", "type": "text"},
            {"key": "doctor_name", "type": "text"},
            {"key": "department", "type": "text"},
        ]
    })
    f_med = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Medicine Stock", "slug": "medicine_stock", "status": "ready",
        "columns": [
            {"key": "medicine", "type": "text"},
            {"key": "stock_units", "type": "number"},
        ]
    })

    rows_w = business_database_writer["company_rows"]
    await rows_w.insert_many([
        {"company_id": company_id, "file_id": str(f_app.inserted_id), "data": {"appointment_id": "A1", "doctor": "Dr. Anil Kulkarni", "patient_name": "Vijay"}},
        {"company_id": company_id, "file_id": str(f_app.inserted_id), "data": {"appointment_id": "A2", "doctor": "Dr. Priya Roy", "patient_name": "Anita"}},
        {"company_id": company_id, "file_id": str(f_doc.inserted_id), "data": {"doctor_id": "D1", "doctor_name": "Dr. Anil Kulkarni", "department": "Cardiology"}},
        {"company_id": company_id, "file_id": str(f_doc.inserted_id), "data": {"doctor_id": "D2", "doctor_name": "Dr. Priya Roy", "department": "Pediatrics"}},
        {"company_id": company_id, "file_id": str(f_med.inserted_id), "data": {"medicine": "Paracetamol", "stock_units": 100}},
    ])

    orig_gen, orig_md, orig_fu = container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service
    container.query_service.generator = FakeSQLGenerator()
    container.query_service.multidoc_service = None
    container.query_service.followup_service = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
            c_res = await client.post("/v1/company/connections", json={
                "left_file_id": str(f_app.inserted_id), "left_key": "doctor",
                "right_file_id": str(f_doc.inserted_id), "right_key": "doctor_name", "name": "Appointments->Doctors"
            }, headers=headers)
            assert c_res.status_code == 201
            await container.enrichment_worker.run_enrichment(company_id, c_res.json()["id"])

            # Negative test: querying Medicine Stock with Appointments has no connection
            res_neg = await client.post("/v1/chat/query", json={"question": "Which medicine was prescribed in each appointment?"}, headers=headers)
            assert res_neg.status_code == 200 and res_neg.json()["status"] == "refused"
            assert res_neg.json()["refusal_reason"] == "no_connection"
    finally:
        container.query_service.generator, container.query_service.multidoc_service, container.query_service.followup_service = orig_gen, orig_md, orig_fu


@pytest.mark.asyncio
async def test_eval_auto_scan_detection(setup_eval_packs):
    fixture = setup_eval_packs
    company_id = fixture["company_id"]
    cookies = {"session": fixture["session"], "csrf_token": fixture["csrf"]}
    headers = {"X-CSRF-Token": fixture["csrf"]}

    # Seed two files with clear matching keys
    f_orders = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Orders", "slug": "orders", "status": "ready",
        "columns": [
            {"key": "order_id", "type": "text", "role": "identifier", "hidden": False},
            {"key": "customer_id", "type": "text", "role": "foreign_key", "hidden": False},
            {"key": "total", "type": "number", "role": "measure", "hidden": False},
        ]
    })
    f_custs = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Customers", "slug": "customers", "status": "ready",
        "columns": [
            {"key": "customer_id", "type": "text", "role": "identifier", "hidden": False},
            {"key": "name", "type": "text", "role": "text", "hidden": False},
            {"key": "segment", "type": "text", "role": "category", "hidden": False},
        ]
    })

    rows_w = business_database_writer["company_rows"]
    await rows_w.insert_many([
        {"company_id": company_id, "file_id": str(f_orders.inserted_id), "data": {"order_id": "O1", "customer_id": "C1", "total": 100}},
        {"company_id": company_id, "file_id": str(f_orders.inserted_id), "data": {"order_id": "O2", "customer_id": "C2", "total": 200}},
        {"company_id": company_id, "file_id": str(f_custs.inserted_id), "data": {"customer_id": "C1", "name": "Acme", "segment": "Retail"}},
        {"company_id": company_id, "file_id": str(f_custs.inserted_id), "data": {"customer_id": "C2", "name": "Globex", "segment": "Corporate"}},
        {"company_id": company_id, "file_id": str(f_custs.inserted_id), "data": {"customer_id": "C3", "name": "Initech", "segment": "Corporate"}},
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as client:
        # Trigger scan
        scan_res = await client.post("/v1/company/connections/scan", headers=headers)
        assert scan_res.status_code == 202
        assert scan_res.json()["status"] in ("scanning", "running")

        # Poll scan state until completed
        import asyncio
        for _ in range(20):
            await asyncio.sleep(0.5)
            state_res = await client.get("/v1/company/connections/scan")
            if state_res.status_code == 200 and state_res.json().get("status") == "completed":
                break
        assert state_res.status_code == 200
        assert state_res.json()["status"] == "completed"

        # Verify suggested connection was created
        conns_res = await client.get("/v1/company/connections")
        assert conns_res.status_code == 200
        conns = conns_res.json()["connections"]
        assert len(conns) >= 1
        auto_conn = next((c for c in conns if c.get("origin") == "auto"), None)
        assert auto_conn is not None
        assert auto_conn["confidence"] >= 0.6
        assert auto_conn["left"]["key"] == "customer_id"
        assert auto_conn["right"]["key"] == "customer_id"
