"""
Phase 6 Live Check: Multi-Turn Conversation Memory and Follow-Up Resolution.
Plays multi-turn conversation on BrightPath test data and verifies isolation.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from app.main import app
from app.core.security import generate_csrf_token, generate_opaque_token, hash_token
from app.infrastructure.db.mongodb import (
    company_files_collection,
    business_database_writer,
    sessions_collection,
    tenants_collection,
    users_collection,
)


async def run_live_followup_check():
    company_id = "live-bp-tenant"
    admin_a = "live_bp_admin_a"
    user_b = "live_bp_user_b"

    # Setup BrightPath tenant and users
    await tenants_collection.update_one(
        {"_id": company_id},
        {"$set": {"name": "BrightPath High", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    admin_email = "live_admin_bp@example.com"
    other_email = "live_other_bp@example.com"

    await users_collection.delete_many({"email": {"$in": [admin_email, other_email]}})
    await users_collection.update_one(
        {"_id": admin_a},
        {"$set": {"email": admin_email, "role": "company_admin", "company_id": company_id, "is_active": True}},
        upsert=True,
    )
    await users_collection.update_one(
        {"_id": user_b},
        {"$set": {"email": other_email, "role": "employee", "company_id": company_id, "is_active": True}},
        upsert=True,
    )

    now = datetime.now(timezone.utc)
    raw_token_a = generate_opaque_token()
    csrf_token_a = generate_csrf_token()
    await sessions_collection.insert_one({
        "user_id": admin_a, "token_hash": hash_token(raw_token_a),
        "expires_at": now + timedelta(hours=2), "ip": "127.0.0.1", "created_at": now,
    })

    raw_token_b = generate_opaque_token()
    csrf_token_b = generate_csrf_token()
    await sessions_collection.insert_one({
        "user_id": user_b, "token_hash": hash_token(raw_token_b),
        "expires_at": now + timedelta(hours=2), "ip": "127.0.0.1", "created_at": now,
    })

    # Seed students, exam marks, and teachers
    f1 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Students", "slug": "brightpath_students", "status": "ready",
        "columns": [{"key": "student_name", "type": "text"}, {"key": "class", "type": "text"}, {"key": "section", "type": "text"}, {"key": "fee_status", "type": "text"}]
    })
    f2 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Exam Marks", "slug": "brightpath_exam_marks", "status": "ready",
        "columns": [{"key": "student_name", "type": "text"}, {"key": "subject", "type": "text"}, {"key": "class", "type": "text"}, {"key": "marks_obtained", "type": "number"}]
    })
    f3 = await company_files_collection.insert_one({
        "company_id": company_id, "name": "Teachers", "slug": "brightpath_teachers", "status": "ready",
        "columns": [{"key": "teacher_name", "type": "text"}, {"key": "subject", "type": "text"}]
    })

    rows_w = business_database_writer["company_rows"]
    # 9 students in Class 8 (4 in A, 5 in B), 11 students with Pending fee
    student_rows = []
    for i in range(4):
        student_rows.append({"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"student_name": f"S8A_{i}", "class": "Class 8", "section": "A", "fee_status": "Pending"}})
    for i in range(5):
        student_rows.append({"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"student_name": f"S8B_{i}", "class": "Class 8", "section": "B", "fee_status": "Pending"}})
    for i in range(2):
        student_rows.append({"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"student_name": f"S10_{i}", "class": "Class 10", "section": "A", "fee_status": "Pending"}})
    for i in range(5):
        student_rows.append({"company_id": company_id, "file_id": str(f1.inserted_id), "data": {"student_name": f"S10_paid_{i}", "class": "Class 10", "section": "B", "fee_status": "Paid"}})

    # Exam marks for Class 10
    marks_rows = [
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"student_name": "S1", "subject": "Maths", "class": "Class 10", "marks_obtained": 63}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"student_name": "S1", "subject": "Science", "class": "Class 10", "marks_obtained": 58.7}},
        {"company_id": company_id, "file_id": str(f2.inserted_id), "data": {"student_name": "S1", "subject": "English", "class": "Class 10", "marks_obtained": 66.9}},
    ]
    # Teachers
    teacher_rows = [
        {"company_id": company_id, "file_id": str(f3.inserted_id), "data": {"teacher_name": "T1", "subject": "Maths"}},
        {"company_id": company_id, "file_id": str(f3.inserted_id), "data": {"teacher_name": "T2", "subject": "Science"}},
    ]
    await rows_w.insert_many(student_rows + marks_rows + teacher_rows)

    from app.container import container
    from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator
    from app.infrastructure.azure.fake_followup import FakeFollowUpResolver
    from app.modules.chat.followup import FollowUpService

    orig_gen = container.query_service.generator
    orig_fu = container.query_service.followup_service
    orig_md = container.query_service.multidoc_service

    container.query_service.generator = FakeSQLGenerator()
    container.query_service.followup_service = FollowUpService(resolver=FakeFollowUpResolver())
    container.query_service.multidoc_service = None

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            cookies_a = {"session": raw_token_a, "csrf_token": csrf_token_a}
            headers_a = {"X-CSRF-Token": csrf_token_a}

            # Turn 1
            res1 = await client.post("/v1/chat/query", json={"question": "How many students are in each class?"}, cookies=cookies_a, headers=headers_a)
            data1 = res1.json()
            conv_id = data1.get("conversation_id")
            print(f"Turn 1 Q: 'How many students are in each class?' -> status: {data1.get('status')}, conv_id: {conv_id}")

            # Turn 2
            res2 = await client.post("/v1/chat/query", json={"question": "Only for Class 8", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data2 = res2.json()
            print(f"Turn 2 Q: 'Only for Class 8' -> status: {data2.get('status')}, interpreted: '{data2.get('meta', {}).get('interpreted_question')}', rows: {data2.get('answer', {}).get('rows')}")

            # Turn 3
            res3 = await client.post("/v1/chat/query", json={"question": "And by section?", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data3 = res3.json()
            print(f"Turn 3 Q: 'And by section?' -> status: {data3.get('status')}, interpreted: '{data3.get('meta', {}).get('interpreted_question')}', rows: {data3.get('answer', {}).get('rows')}")

            # Turn 4
            res4 = await client.post("/v1/chat/query", json={"question": "What is the average marks in each subject?", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data4 = res4.json()
            print(f"Turn 4 Q: 'What is the average marks in each subject?' -> status: {data4.get('status')}")

            # Turn 5
            res5 = await client.post("/v1/chat/query", json={"question": "What about only Class 10?", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data5 = res5.json()
            print(f"Turn 5 Q: 'What about only Class 10?' -> status: {data5.get('status')}, interpreted: '{data5.get('meta', {}).get('interpreted_question')}'")

            # Turn 6
            res6 = await client.post("/v1/chat/query", json={"question": "Which subject is the lowest?", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data6 = res6.json()
            print(f"Turn 6 Q: 'Which subject is the lowest?' -> status: {data6.get('status')}, interpreted: '{data6.get('meta', {}).get('interpreted_question')}'")

            # Turn 7
            res7 = await client.post("/v1/chat/query", json={"question": "How many students are in each fee status?", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data7 = res7.json()
            print(f"Turn 7 Q: 'How many students are in each fee status?' -> status: {data7.get('status')}")

            # Turn 8
            res8 = await client.post("/v1/chat/query", json={"question": "Show only Pending", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data8 = res8.json()
            print(f"Turn 8 Q: 'Show only Pending' -> status: {data8.get('status')}, interpreted: '{data8.get('meta', {}).get('interpreted_question')}'")

            # Turn 9: Topic switch to teachers
            res9 = await client.post("/v1/chat/query", json={"question": "How many teachers teach each subject?", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data9 = res9.json()
            print(f"Turn 9 Q: 'How many teachers teach each subject?' -> status: {data9.get('status')}")

            # Turn 10: Positional reference
            res10 = await client.post("/v1/chat/query", json={"question": "Show the second one", "conversation_id": conv_id}, cookies=cookies_a, headers=headers_a)
            data10 = res10.json()
            print(f"Turn 10 Q: 'Show the second one' -> status: {data10.get('status')}, clarification_question: '{data10.get('answer', {}).get('clarification_question')}'")

            # Isolation check: User B tries to use conv_id created by Admin A -> 404
            cookies_b = {"session": raw_token_b, "csrf_token": csrf_token_b}
            headers_b = {"X-CSRF-Token": csrf_token_b}
            res_iso = await client.post("/v1/chat/query", json={"question": "Only for Class 8", "conversation_id": conv_id}, cookies=cookies_b, headers=headers_b)
            print(f"Isolation check: User B with Admin A conv_id -> HTTP status: {res_iso.status_code}")
            assert res_iso.status_code == 404
    finally:
        container.query_service.generator = orig_gen
        container.query_service.followup_service = orig_fu
        container.query_service.multidoc_service = orig_md
        await company_files_collection.delete_many({"company_id": company_id})
        await business_database_writer["company_rows"].delete_many({"company_id": company_id})
        await sessions_collection.delete_many({"user_id": {"$in": [admin_a, user_b]}})
        await users_collection.delete_many({"_id": {"$in": [admin_a, user_b]}})
        await tenants_collection.delete_one({"_id": company_id})
        print("\nLive follow-up verification check completed successfully.")


if __name__ == "__main__":
    asyncio.run(run_live_followup_check())
