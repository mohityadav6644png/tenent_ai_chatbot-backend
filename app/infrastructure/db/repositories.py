"""
Database Repositories (app/infrastructure/db/repositories.py)
Repository implementations for Auth, Tenants, Users, and Audit collections.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId
from pymongo import ReturnDocument
from app.infrastructure.azure.redis_cache import get_cache_client
from app.infrastructure.db.mongodb import (
    audit_events_collection,
    invite_tokens_collection,
    invites_collection,
    password_resets_collection,
    sessions_collection,
    tenants_collection,
    users_collection,
)
from app.infrastructure.db.tenant_user_repo import (
    MongoTenantRepository,
    MongoUserRepository,
)


class MongoAuthRepository:
    def __init__(self):
        self._cache = get_cache_client()

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return await users_collection.find_one({"email": email.strip().lower()})

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        candidates = [str(user_id)]
        if ObjectId.is_valid(user_id):
            candidates.append(ObjectId(user_id))
        return await users_collection.find_one({"_id": {"$in": candidates}})

    async def update_user(self, user_id: str, updates: Dict[str, Any]) -> None:
        updates["updated_at"] = datetime.now(timezone.utc)
        candidates = [str(user_id)]
        if ObjectId.is_valid(user_id):
            candidates.append(ObjectId(user_id))
        await users_collection.update_one({"_id": {"$in": candidates}}, {"$set": updates})

    async def update_user_scoped(self, user_id: str, company_id: str, updates: Dict[str, Any]) -> None:
        updates["updated_at"] = datetime.now(timezone.utc)
        candidates = [str(user_id)]
        if ObjectId.is_valid(user_id):
            candidates.append(ObjectId(user_id))
        await users_collection.update_one({"_id": {"$in": candidates}, "company_id": str(company_id)}, {"$set": updates})

    async def create_session(self, doc: Dict[str, Any]) -> None:
        await sessions_collection.insert_one(doc)

    async def get_session_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await sessions_collection.find_one({"token_hash": token_hash})

    async def delete_session(self, token_hash: str) -> None:
        await sessions_collection.delete_one({"token_hash": token_hash})

    async def delete_user_sessions(self, user_id: str) -> None:
        await sessions_collection.delete_many({"user_id": str(user_id)})

    async def get_tenant_by_id(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        candidates = [str(tenant_id)]
        if ObjectId.is_valid(tenant_id):
            candidates.append(ObjectId(tenant_id))
        return await tenants_collection.find_one({"_id": {"$in": candidates}})

    async def update_tenant(self, tenant_id: str, updates: Dict[str, Any]) -> None:
        updates["updated_at"] = datetime.now(timezone.utc)
        candidates = [str(tenant_id)]
        if ObjectId.is_valid(tenant_id):
            candidates.append(ObjectId(tenant_id))
        await tenants_collection.update_one({"_id": {"$in": candidates}}, {"$set": updates})

    async def get_invite_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await invites_collection.find_one({"token_hash": token_hash, "status": "pending"})

    async def claim_invite_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        claimed = await invite_tokens_collection.find_one_and_update(
            {"token_hash": token_hash, "used": False, "expires_at": {"$gt": now}},
            {"$set": {"used": True, "used_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed:
            return claimed
        return await invites_collection.find_one_and_update(
            {"token_hash": token_hash, "status": "pending", "expires_at": {"$gt": now}},
            {"$set": {"status": "accepted", "used": True, "used_at": now}},
            return_document=ReturnDocument.AFTER,
        )

    async def create_invite_token(self, doc: Dict[str, Any]) -> None:
        await invite_tokens_collection.insert_one(doc)

    async def update_invite(self, invite_id: str, updates: Dict[str, Any]) -> None:
        oid = ObjectId(invite_id) if ObjectId.is_valid(invite_id) else invite_id
        await invites_collection.update_one({"_id": oid}, {"$set": updates})

    async def create_password_reset(self, doc: Dict[str, Any]) -> None:
        await password_resets_collection.insert_one(doc)

    async def get_password_reset_by_hash(self, token_hash: str) -> Optional[Dict[str, Any]]:
        return await password_resets_collection.find_one({"token_hash": token_hash, "used": False})

    async def mark_password_reset_used(self, reset_id: str) -> None:
        oid = ObjectId(reset_id) if ObjectId.is_valid(reset_id) else reset_id
        await password_resets_collection.update_one({"_id": oid}, {"$set": {"used": True}})

    async def check_failed_login(self, email: str, ip: str) -> int:
        val = await self._cache.get(f"failed_login:{email.lower().strip()}:{ip}")
        return int(val) if val else 0

    async def record_failed_login(self, email: str, ip: str) -> int:
        k = f"failed_login:{email.lower().strip()}:{ip}"
        count = await self._cache.incr(k)
        if count == 1:
            await self._cache.expire(k, 900)
        return count

    async def clear_failed_login(self, email: str, ip: str) -> None:
        await self._cache.delete(f"failed_login:{email.lower().strip()}:{ip}")


class MongoAuditRepository:
    async def record_event(self, doc: Dict[str, Any]) -> None:
        await audit_events_collection.insert_one(doc)

    async def list_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        docs = await audit_events_collection.find().sort("occurred_at", -1).limit(limit).to_list(limit)
        return [{"id": str(d["_id"]), "occurred_at": d["occurred_at"], "actor": d["actor"], "action": d["action"], "target": d["target"], "result": d.get("result", "success")} for d in docs]


# Re-export SchemaCatalogRepo and Repositories
from app.infrastructure.db.catalog_repo import SchemaCatalogRepo

__all__ = [
    "MongoAuthRepository",
    "MongoTenantRepository",
    "MongoUserRepository",
    "MongoAuditRepository",
    "SchemaCatalogRepo",
]
