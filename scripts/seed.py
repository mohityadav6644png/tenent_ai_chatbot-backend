"""
Database Seeding Script (scripts/seed.py)
Seeds Acme Traders and Bharat Foods tenants, users, invites, reports, audit events,
and 6+ months of MongoDB business data for full end-to-end API testing.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from bson import ObjectId

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infrastructure.db.mongodb import (
    audit_events_collection,
    close_mongodb_connection,
    connect_to_mongodb,
    conversations_collection,
    invites_collection,
    messages_collection,
    password_resets_collection,
    reports_collection,
    tenants_collection,
    users_collection,
)
from scripts.seed_business import seed_business_data
from scripts.seed_constants import (
    ACME_ID,
    BHARAT_ID,
    DEMO_COMPANYADMIN_HASH,
    DEMO_EMPLOYEE_HASH,
    DEMO_INVITE_HASH,
    DEMO_PASSWORD_HASH,
    DEMO_RESET_HASH,
    DEMO_SUPERADMIN_HASH,
    SUSPENDED_ID,
)


async def seed_app_data():
    await connect_to_mongodb()
    now = datetime.now(timezone.utc)

    # 1. Tenants
    tenants = [
        {"_id": ACME_ID, "name": "Acme Traders", "status": "active", "monthly_query_limit": 10000, "queries_month": 42, "schema_version": 1, "created_at": now},
        {"_id": BHARAT_ID, "name": "Bharat Foods", "status": "active", "monthly_query_limit": 10000, "queries_month": 15, "schema_version": 1, "created_at": now},
        {"_id": SUSPENDED_ID, "name": "Global Retail", "status": "suspended", "monthly_query_limit": 5000, "queries_month": 0, "schema_version": 1, "created_at": now},
    ]
    for t in tenants:
        await tenants_collection.update_one({"_id": t["_id"]}, {"$set": t}, upsert=True)

    # 2. Users (Both project credentials and frontend default demo credentials)
    users = [
        {"email": "owner@platform.com", "name": "Platform Owner", "role": "super_admin", "company_id": None, "password": DEMO_PASSWORD_HASH, "is_active": True, "created_at": now},
        {"email": "superadmin@example.com", "name": "Demo Super Admin", "role": "super_admin", "company_id": None, "password": DEMO_SUPERADMIN_HASH, "is_active": True, "created_at": now},
        {"email": "asha@acme.in", "name": "Asha Patel", "role": "company_admin", "company_id": ACME_ID, "password": DEMO_PASSWORD_HASH, "is_active": True, "created_at": now},
        {"email": "companyadmin@example.com", "name": "Ravi Singh", "role": "company_admin", "company_id": ACME_ID, "password": DEMO_COMPANYADMIN_HASH, "is_active": True, "created_at": now},
        {"email": "employee@acme.in", "name": "Rohan Sharma", "role": "employee", "company_id": ACME_ID, "password": DEMO_PASSWORD_HASH, "is_active": True, "created_at": now},
        {"email": "demo@example.com", "name": "Demo Employee", "role": "employee", "company_id": ACME_ID, "password": DEMO_EMPLOYEE_HASH, "is_active": True, "created_at": now},
        {"email": "vikram@bharat.in", "name": "Vikram Patel", "role": "company_admin", "company_id": BHARAT_ID, "password": DEMO_PASSWORD_HASH, "is_active": True, "created_at": now},
    ]
    for u in users:
        await users_collection.update_one({"email": u["email"]}, {"$set": u}, upsert=True)

    # 3. Pending Invites
    await invites_collection.delete_many({"company_id": ACME_ID})
    inv_doc = {
        "_id": ObjectId("6aed88887c9b4d71857865aa"),
        "company_id": ACME_ID,
        "email": "newhire@acme.in",
        "name": "Karan Mehra",
        "role": "employee",
        "token_hash": DEMO_INVITE_HASH,
        "status": "pending",
        "expires_at": now + timedelta(days=7),
        "created_at": now,
    }
    await invites_collection.insert_one(inv_doc)

    # 4. Password Resets
    emp = await users_collection.find_one({"email": "employee@acme.in"})
    if emp:
        await password_resets_collection.delete_many({"email": "employee@acme.in"})
        await password_resets_collection.insert_one({
            "user_id": str(emp["_id"]),
            "email": "employee@acme.in",
            "token_hash": DEMO_RESET_HASH,
            "used": False,
            "expires_at": now + timedelta(hours=2),
            "created_at": now,
        })

    # 5. Scheduled Reports
    await reports_collection.delete_many({"company_id": ACME_ID})
    reports = [
        {"_id": ObjectId("6aed694d7c9b4d71857865ab"), "company_id": ACME_ID, "name": "Weekly Sales Summary", "query": "Weekly sales by region", "schedule": {"frequency": "weekly", "day": "monday"}, "recipients": [{"email": "asha@acme.in"}], "status": "active", "created_at": now},
        {"_id": ObjectId("6aed694d7c9b4d71857865ac"), "company_id": ACME_ID, "name": "Low Stock Alert", "query": "Products below threshold", "schedule": {"frequency": "daily", "time": "09:00"}, "recipients": [{"email": "asha@acme.in"}], "status": "active", "created_at": now},
        {"_id": ObjectId("6aed694d7c9b4d71857865ad"), "company_id": ACME_ID, "name": "Monthly Overdue Invoices", "query": "Overdue invoices", "schedule": {"frequency": "monthly"}, "recipients": [{"email": "asha@acme.in"}], "status": "paused", "created_at": now},
    ]
    await reports_collection.insert_many(reports)

    # 6. Conversations & Messages
    if emp:
        await conversations_collection.delete_many({"user_id": str(emp["_id"])})
        conv_id = ObjectId("6aed79f07c9b4d71857865b1")
        msg_id = ObjectId("6aed79f07c9b4d71857865b2")
        await conversations_collection.insert_one({"_id": conv_id, "user_id": str(emp["_id"]), "company_id": ACME_ID, "title": "Total completed orders", "created_at": now})
        await messages_collection.delete_many({"user_id": str(emp["_id"])})
        await messages_collection.insert_one({"_id": msg_id, "conversation_id": str(conv_id), "user_id": str(emp["_id"]), "question": "Total completed orders", "decision": "answer", "created_at": now})

    # 7. Audit Events
    await audit_events_collection.delete_many({})
    await audit_events_collection.insert_many([
        {"occurred_at": now - timedelta(hours=3), "actor": "owner@platform.com", "action": "TENANT_CREATE", "target": "Acme Traders", "result": "success"},
        {"occurred_at": now - timedelta(hours=2), "actor": "asha@acme.in", "action": "USER_INVITE", "target": "newhire@acme.in", "result": "success"},
        {"occurred_at": now - timedelta(minutes=45), "actor": "owner@platform.com", "action": "SCHEMA_SYNC", "target": ACME_ID, "result": "success"},
    ])
    print("[OK] App database seeded with tenants, users, invites, reports, and audit events.")


async def main():
    await seed_app_data()
    await seed_business_data()
    await close_mongodb_connection()


if __name__ == "__main__":
    asyncio.run(main())
