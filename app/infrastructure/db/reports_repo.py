"""
Reports Repository (app/infrastructure/db/reports_repo.py)
MongoDB persistence for scheduled reports and email recipient lookups.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId
from app.infrastructure.db.mongodb import reports_collection, users_collection
from app.modules.reports.models import create_report_document


class MongoReportsRepository:
    async def list_reports(self, company_id: str) -> List[Dict[str, Any]]:
        await self.seed_reports_if_empty(company_id)
        return await reports_collection.find({"company_id": str(company_id)}).to_list(100)

    async def get_report_scoped(self, company_id: str, report_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(report_id) if ObjectId.is_valid(report_id) else report_id
        return await reports_collection.find_one({"_id": oid, "company_id": str(company_id)})

    async def get_report_global(self, report_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(report_id) if ObjectId.is_valid(report_id) else report_id
        return await reports_collection.find_one({"_id": oid})

    async def update_report(self, report_id: str, updates: Dict[str, Any]) -> None:
        updates["updated_at"] = datetime.now(timezone.utc)
        oid = ObjectId(report_id) if ObjectId.is_valid(report_id) else report_id
        await reports_collection.update_one({"_id": oid}, {"$set": updates})

    async def get_user_emails(self, user_ids: List[str]) -> List[str]:
        oids = [ObjectId(uid) for uid in user_ids if ObjectId.is_valid(uid)]
        users = await users_collection.find({"_id": {"$in": oids}}).to_list(100)
        return [u["email"] for u in users if u.get("email")]

    async def seed_reports_if_empty(self, company_id: str) -> None:
        count = await reports_collection.count_documents({"company_id": str(company_id)})
        if count > 0:
            return

        # Fetch an admin user to be default recipient
        admin = await users_collection.find_one({"company_id": str(company_id)})
        recipients = [{"id": str(admin["_id"]), "name": admin.get("name", "Admin")}] if admin else []

        sample_reports = [
            create_report_document(
                company_id=company_id,
                name="Daily Sales Summary",
                query="What is our total revenue today?",
                schedule={"frequency": "daily", "time": "09:00"},
                recipients=recipients,
            ),
            create_report_document(
                company_id=company_id,
                name="Weekly Inventory & Low Stock Alert",
                query="Which products are low on stock?",
                schedule={"frequency": "weekly", "time": "08:00", "weekday": 1},
                recipients=recipients,
            ),
            create_report_document(
                company_id=company_id,
                name="Monthly Revenue & Overdue Invoices",
                query="Show overdue unpaid invoices",
                schedule={"frequency": "monthly", "time": "07:00", "day_of_month": 1},
                recipients=recipients,
            ),
        ]
        await reports_collection.insert_many(sample_reports)
