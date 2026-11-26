"""
Phase 8 Live Check: Progressive User Profiling.
Plays scripted conversation verifying lead capture, slot filling, skip, opt-out, and endpoints.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from app.main import app
from app.core.config import settings
from app.core.security import generate_csrf_token, generate_opaque_token, hash_token
from app.infrastructure.db.mongodb import (
    company_files_collection,
    business_database_writer,
    sessions_collection,
    tenants_collection,
    users_collection,
)
from app.container import container
from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator
from app.infrastructure.azure.fake_followup import FakeFollowUpResolver
from app.infrastructure.profile_extractor.fake_extractor import FakeProfileExtractor
from app.modules.chat.followup import FollowUpService
from app.modules.profile.service import ProfileService


async def run_live_profile_check():
    company_id = "live-profile-tenant"
    user_id = "live_profile_user"
    user_email = "live_profile_test@example.com"

    # Enforce settings for the live check
    settings.PROFILE_PREFILL_FROM_ACCOUNT = False
    settings.PROFILE_ENABLED = True
    settings.PROFILE_MIN_ANSWERS_BEFORE_FIRST_ASK = 2
    settings.PROFILE_ASK_EVERY_N_ANSWERS = 3
    settings.PROFILE_FIELDS_ORDER = "name,company_name,interest"

    # Setup tenant and user
    await tenants_collection.update_one(
        {"_id": company_id},
        {"$set": {"name": "Test Acme High", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    await users_collection.delete_many({"email": user_email})
    await users_collection.update_one(
        {"_id": user_id},
        {"$set": {"email": user_email, "role": "employee", "company_id": company_id, "is_active": True}},
        upsert=True,
    )

    now = datetime.now(timezone.utc)
    raw_token = generate_opaque_token()
    csrf_token = generate_csrf_token()
    await sessions_collection.delete_many({"user_id": user_id})
    await sessions_collection.insert_one({
        "user_id": user_id,
        "token_hash": hash_token(raw_token),
        "expires_at": now + timedelta(hours=2),
        "ip": "127.0.0.1",
        "created_at": now,
    })

    # Seed files: Students, Exam Marks, Teachers, and Sales
    f_students = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Students", "slug": "brightpath_students", "status": "ready",
        "columns": [
            {"key": "student_name", "type": "text"},
            {"key": "class", "type": "text"},
            {"key": "section", "type": "text"},
            {"key": "fee_status", "type": "text"}
        ]
    })
    f_marks = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Exam Marks", "slug": "brightpath_exam_marks", "status": "ready",
        "columns": [
            {"key": "student_name", "type": "text"},
            {"key": "subject", "type": "text"},
            {"key": "class", "type": "text"},
            {"key": "marks_obtained", "type": "number"}
        ]
    })
    f_teachers = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Teachers", "slug": "brightpath_teachers", "status": "ready",
        "columns": [
            {"key": "teacher_name", "type": "text"},
            {"key": "subject", "type": "text"}
        ]
    })
    f_sales = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Sales", "slug": "acme_sales", "status": "ready",
        "columns": [
            {"key": "region", "type": "text"},
            {"key": "sales", "type": "number"}
        ]
    })

    rows_w = business_database_writer["company_rows"]
    student_rows = [
        {"company_id": company_id, "file_id": str(f_students.inserted_id), "data": {"student_name": f"S8_{i}", "class": "Class 8", "section": "A", "fee_status": "Pending"}}
        for i in range(5)
    ]
    marks_rows = [
        {"company_id": company_id, "file_id": str(f_marks.inserted_id), "data": {"student_name": "S1", "subject": "Maths", "class": "Class 10", "marks_obtained": 80}},
        {"company_id": company_id, "file_id": str(f_marks.inserted_id), "data": {"student_name": "S2", "subject": "Science", "class": "Class 10", "marks_obtained": 90}},
    ]
    teacher_rows = [
        {"company_id": company_id, "file_id": str(f_teachers.inserted_id), "data": {"teacher_name": "T1", "subject": "Maths"}},
    ]
    sales_rows = [
        {"company_id": company_id, "file_id": str(f_sales.inserted_id), "data": {"region": "North", "sales": 1500}},
        {"company_id": company_id, "file_id": str(f_sales.inserted_id), "data": {"region": "South", "sales": 2200}},
    ]
    await rows_w.insert_many(student_rows + marks_rows + teacher_rows + sales_rows)

    # Backup & substitute mock components
    orig_gen = container.query_service.generator
    orig_fu = container.query_service.followup_service
    orig_prof = container.query_service.profile_service
    orig_md = container.query_service.multidoc_service

    fake_extractor = FakeProfileExtractor()
    container.query_service.generator = FakeSQLGenerator()
    container.query_service.followup_service = FollowUpService(resolver=FakeFollowUpResolver())
    container.query_service.profile_service = ProfileService(repo=container.profile_repo, extractor=fake_extractor)
    container.query_service.multidoc_service = None

    llm_calls_count = 0

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            cookies = {"session": raw_token, "csrf_token": csrf_token}
            headers = {"X-CSRF-Token": csrf_token}

            # Turn 1: Analytics question 1
            res1 = await client.post("/v1/chat/query", json={"question": "How many students are in each class?"}, cookies=cookies, headers=headers)
            llm_calls_count += 1
            data1 = res1.json()
            conv_id = data1.get("conversation_id")
            p1 = data1.get("profile_prompt")
            print(f"Turn 1 -> status: {data1.get('status')}, prompt: {p1.get('field') if p1 else None}")
            assert data1.get("status") == "success"
            assert p1 is None

            # Turn 2: Analytics question 2 -> prompts for name after 2nd answer
            res2 = await client.post("/v1/chat/query", json={"question": "What is the average marks in each subject?", "conversation_id": conv_id}, cookies=cookies, headers=headers)
            llm_calls_count += 1
            data2 = res2.json()
            p2 = data2.get("profile_prompt")
            print(f"Turn 2 -> status: {data2.get('status')}, prompt: {p2.get('field') if p2 else None}")
            assert data2.get("status") == "success"
            assert p2 is not None and p2.get("field") == "name"

            # Turn 3: Reply "I'm Mohit"
            res3 = await client.post("/v1/chat/query", json={"question": "I'm Mohit", "conversation_id": conv_id}, cookies=cookies, headers=headers)
            llm_calls_count += 1
            data3 = res3.json()
            p3 = data3.get("profile_prompt")
            prof_check = await container.profile_repo.get_profile(company_id, user_id)
            name_st = prof_check.fields["name"].status if prof_check else None
            print(f"Turn 3 -> status: {data3.get('status')}, name_status: {name_st}, prompt: {p3.get('field') if p3 else None}")
            assert data3.get("status") == "success"
            assert name_st == "collected"
            assert p3 is None

            # Turn 4: Question 3
            res4 = await client.post("/v1/chat/query", json={"question": "How many students are in each fee status?", "conversation_id": conv_id}, cookies=cookies, headers=headers)
            llm_calls_count += 1
            data4 = res4.json()
            p4 = data4.get("profile_prompt")
            print(f"Turn 4 -> status: {data4.get('status')}, prompt: {p4.get('field') if p4 else None}")
            assert data4.get("status") == "success"
            assert p4 is None

            # Turn 5: Question 4
            res5 = await client.post("/v1/chat/query", json={"question": "How many teachers teach each subject?", "conversation_id": conv_id}, cookies=cookies, headers=headers)
            llm_calls_count += 1
            data5 = res5.json()
            p5 = data5.get("profile_prompt")
            print(f"Turn 5 -> status: {data5.get('status')}, prompt: {p5.get('field') if p5 else None}")
            assert data5.get("status") == "success"
            assert p5 is None

            # Turn 6: Question 5 -> prompts for company_name after 3 answers
            res6 = await client.post("/v1/chat/query", json={"question": "How many students are in Class 8?", "conversation_id": conv_id}, cookies=cookies, headers=headers)
            llm_calls_count += 1
            data6 = res6.json()
            p6 = data6.get("profile_prompt")
            print(f"Turn 6 -> status: {data6.get('status')}, prompt: {p6.get('field') if p6 else None}")
            assert data6.get("status") == "success"
            assert p6 is not None and p6.get("field") == "company_name"

            # Turn 7: Reply "skip"
            res7 = await client.post("/v1/chat/query", json={"question": "skip", "conversation_id": conv_id}, cookies=cookies, headers=headers)
            data7 = res7.json()
            p7 = data7.get("profile_prompt")
            prof_check2 = await container.profile_repo.get_profile(company_id, user_id)
            comp_st = prof_check2.fields["company_name"].status if prof_check2 else None
            print(f"Turn 7 -> status: {data7.get('status')}, company_name_status: {comp_st}, prompt: {p7.get('field') if p7 else None}")
            assert comp_st == "skipped"
            assert p7 is None

            # Turn 8: Reply "Don't ask me again"
            res8 = await client.post("/v1/chat/query", json={"question": "Don't ask me again", "conversation_id": conv_id}, cookies=cookies, headers=headers)
            data8 = res8.json()
            p8 = data8.get("profile_prompt")
            prof_check3 = await container.profile_repo.get_profile(company_id, user_id)
            opted = prof_check3.opted_out if prof_check3 else False
            print(f"Turn 8 -> status: {data8.get('status')}, opted_out: {opted}, prompt: {p8.get('field') if p8 else None}")
            assert opted is True
            assert p8 is None

            # Turn 9: New conversation with volunteered details
            res9 = await client.post(
                "/v1/chat/query",
                json={"question": "Hi, I'm Priya from Acme, show me total sales by region"},
                cookies=cookies,
                headers=headers
            )
            llm_calls_count += 2  # 1 extractor call + 1 generator call
            data9 = res9.json()
            p9 = data9.get("profile_prompt")
            prof_check4 = await container.profile_repo.get_profile(company_id, user_id)
            name_val = prof_check4.fields["name"].value if prof_check4 else None
            comp_val = prof_check4.fields["company_name"].value if prof_check4 else None
            headline = data9.get("answer", {}).get("headline", "")
            print(f"Turn 9 -> status: {data9.get('status')}, name: {name_val}, company: {comp_val}, answer: '{headline}', prompt: {p9.get('field') if p9 else None}")
            assert data9.get("status") == "success"
            assert name_val == "Priya"
            assert comp_val == "Acme"
            assert len(headline) > 0

            # Endpoint check: GET /v1/profile
            get_res = await client.get("/v1/profile", cookies=cookies, headers=headers)
            print(f"GET /v1/profile -> HTTP status: {get_res.status_code}")
            assert get_res.status_code == 200
            prof_body = get_res.json()
            assert prof_body["fields"]["name"]["value"] == "Priya"
            assert prof_body["fields"]["company_name"]["value"] == "Acme"

            # Endpoint check: DELETE /v1/profile
            del_res = await client.delete("/v1/profile", cookies=cookies, headers=headers)
            print(f"DELETE /v1/profile -> HTTP status: {del_res.status_code}")
            assert del_res.status_code == 204

            # Verify profile is deleted
            prof_after_del = await container.profile_repo.get_profile(company_id, user_id)
            print(f"Profile after deletion: {prof_after_del}")
            assert prof_after_del is None

            print(f"\nLive check completed successfully! Total scripted LLM calls: {llm_calls_count}")
    finally:
        container.query_service.generator = orig_gen
        container.query_service.followup_service = orig_fu
        container.query_service.profile_service = orig_prof
        container.query_service.multidoc_service = orig_md
        await company_files_collection.delete_many({"company_id": company_id})
        await business_database_writer["company_rows"].delete_many({"company_id": company_id})
        await sessions_collection.delete_many({"user_id": user_id})
        await users_collection.delete_many({"_id": user_id})
        await tenants_collection.delete_one({"_id": company_id})
        await container.profile_repo.delete_profile_and_states(company_id, user_id)


if __name__ == "__main__":
    asyncio.run(run_live_profile_check())
