"""
Tenant and User Repositories (app/infrastructure/db/tenant_user_repo.py)
MongoDB implementations for Tenant lifecycle and Company-scoped User/Invite operations.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId
from app.core.exceptions import UserExistsError
from app.infrastructure.db.mongodb import (
    invites_collection,
    tenants_collection,
    users_collection,
    sessions_collection,
)


class MongoTenantRepository:
    async def list_tenants(self) -> List[Dict[str, Any]]:
        docs = await tenants_collection.find().to_list(1000)
        res = []
        for d in docs:
            cid = str(d["_id"])
            uc = await users_collection.count_documents({"company_id": cid, "is_active": True})
            res.append({"id": cid, "name": d["name"], "status": d.get("status", "active"), "user_count": uc, "queries_month": d.get("queries_month", 0)})
        return res

    async def get_tenant_by_id(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(tenant_id) if ObjectId.is_valid(tenant_id) else tenant_id
        return await tenants_collection.find_one({"_id": oid})

    async def get_tenant_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        return await tenants_collection.find_one({"name": {"$regex": f"^{name.strip()}$", "$options": "i"}})

    async def create_tenant_atomic(self, tenant_doc: Dict[str, Any], admin_doc: Dict[str, Any], invite_doc: Dict[str, Any]) -> str:
        if await users_collection.find_one({"email": admin_doc["email"]}):
            raise UserExistsError("Admin user with this email already exists.")
        await tenants_collection.insert_one(tenant_doc)
        await users_collection.insert_one(admin_doc)
        await invites_collection.insert_one(invite_doc)
        return str(tenant_doc["_id"])

    async def update_tenant(self, tenant_id: str, updates: Dict[str, Any]) -> None:
        updates["updated_at"] = datetime.now(timezone.utc)
        oid = ObjectId(tenant_id) if ObjectId.is_valid(tenant_id) else tenant_id
        await tenants_collection.update_one({"_id": oid}, {"$set": updates})

    async def get_tenant_stats(self, tenant_id: str) -> Dict[str, Any]:
        uc = await users_collection.count_documents({"company_id": tenant_id, "is_active": True})
        t = await self.get_tenant_by_id(tenant_id) or {}
        return {"user_count": uc, "queries_month": t.get("queries_month", 0), "ai_tokens_month": t.get("ai_tokens_month", 0), "avg_response_ms": t.get("avg_response_ms", 120.0)}

    async def get_tenant_admins(self, tenant_id: str) -> List[Dict[str, Any]]:
        admins = await users_collection.find({"company_id": tenant_id, "role": {"$in": ["admin", "company_admin"]}}).to_list(100)
        return [{"id": str(a["_id"]), "name": a.get("name") or a.get("username", "Admin"), "email": a["email"], "last_login_at": a.get("last_login_at")} for a in admins]

    async def bump_schema_version(self, tenant_id: str) -> int:
        oid = ObjectId(tenant_id) if ObjectId.is_valid(tenant_id) else tenant_id
        res = await tenants_collection.find_one_and_update({"_id": oid}, {"$inc": {"schema_version": 1}}, return_document=True)
        return res.get("schema_version", 2) if res else 2

    async def get_platform_summary(self) -> Dict[str, Any]:
        ac = await tenants_collection.count_documents({"status": "active"})
        return {"active_tenants": ac, "api_uptime_pct": 99.98, "avg_response_ms": 115.0, "ai_tokens_month": 450000}


class MongoUserRepository:
    async def list_company_users(self, company_id: str) -> List[Dict[str, Any]]:
        return await users_collection.find({"company_id": str(company_id)}).to_list(1000)

    async def list_company_pending_invites(self, company_id: str) -> List[Dict[str, Any]]:
        return await invites_collection.find({"company_id": str(company_id), "status": "pending"}).to_list(1000)

    async def get_user_by_email_global(self, email: str) -> Optional[Dict[str, Any]]:
        return await users_collection.find_one({"email": email.strip().lower()})

    async def get_pending_invite_by_email(self, company_id: str, email: str) -> Optional[Dict[str, Any]]:
        return await invites_collection.find_one({"company_id": str(company_id), "email": email.strip().lower(), "status": "pending"})

    async def create_user_and_invite(self, user_doc: Dict[str, Any], invite_doc: Dict[str, Any]) -> str:
        await users_collection.insert_one(user_doc)
        res = await invites_collection.insert_one(invite_doc)
        return str(res.inserted_id)

    async def get_invite_by_id_scoped(self, company_id: str, invite_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(invite_id) if ObjectId.is_valid(invite_id) else invite_id
        return await invites_collection.find_one({"_id": oid, "company_id": str(company_id)})

    async def update_invite(self, invite_id: str, updates: Dict[str, Any]) -> None:
        oid = ObjectId(invite_id) if ObjectId.is_valid(invite_id) else invite_id
        await invites_collection.update_one({"_id": oid}, {"$set": updates})

    async def delete_invite(self, invite_id: str) -> None:
        oid = ObjectId(invite_id) if ObjectId.is_valid(invite_id) else invite_id
        await invites_collection.delete_one({"_id": oid})

    async def get_company_user_by_id(self, company_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
        return await users_collection.find_one({"_id": oid, "company_id": str(company_id)})

    async def count_company_active_admins(self, company_id: str) -> int:
        return await users_collection.count_documents({"company_id": str(company_id), "role": {"$in": ["admin", "company_admin"]}, "is_active": True})

    async def deactivate_user(self, user_id: str) -> None:
        oid = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
        await users_collection.update_one({"_id": oid}, {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}})

    async def revoke_user_sessions(self, user_id: str) -> None:
        await sessions_collection.delete_many({"user_id": str(user_id)})
