"""
Chat Repository (app/infrastructure/db/chat_repo.py)
MongoDB persistence for conversations, messages, feedback, and rate limit counters.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId
from app.core.config import settings
from app.core.exceptions import NotFoundError, RateLimitedError
from app.infrastructure.azure.redis_cache import get_cache_client
from app.infrastructure.db.catalog_repo import SchemaCatalogRepo
from app.infrastructure.db.mongodb import (
    conversations_collection,
    feedback_collection,
    messages_collection,
    tenants_collection,
    usage_counters_collection,
)


class MongoChatRepository:
    def __init__(self):
        self._cache = get_cache_client()
        self._catalog_repo = SchemaCatalogRepo()

    async def get_tenant(self, company_id: str) -> Optional[Dict[str, Any]]:
        oid = ObjectId(company_id) if ObjectId.is_valid(company_id) else company_id
        return await tenants_collection.find_one({"_id": oid})

    async def check_and_increment_rate_limits(self, company_id: str, user_id: str) -> None:
        # 1. Per user per minute
        u_key = f"rate:user:{user_id}"
        u_count = await self._cache.incr(u_key)
        if u_count == 1:
            await self._cache.expire(u_key, 60)
        if u_count > settings.RATE_LIMIT_USER_PER_MINUTE:
            raise RateLimitedError("User rate limit exceeded. Please wait a minute.", retry_after=60)

        # 2. Per tenant per minute
        t_key = f"rate:tenant:{company_id}"
        t_count = await self._cache.incr(t_key)
        if t_count == 1:
            await self._cache.expire(t_key, 60)
        if t_count > settings.RATE_LIMIT_TENANT_PER_MINUTE:
            raise RateLimitedError("Organization rate limit exceeded. Please wait a minute.", retry_after=60)

        # 3. Monthly tenant query budget check
        tenant = await self.get_tenant(company_id)
        if tenant:
            monthly_limit = tenant.get("monthly_query_limit", 10000)
            current_month_queries = tenant.get("queries_month", 0)
            if current_month_queries >= monthly_limit:
                raise RateLimitedError("Organization monthly query budget exhausted.", retry_after=3600)

    async def increment_monthly_usage(self, company_id: str, tokens: int = 0) -> None:
        oid = ObjectId(company_id) if ObjectId.is_valid(company_id) else company_id
        await tenants_collection.update_one(
            {"_id": oid},
            {"$inc": {"queries_month": 1, "ai_tokens_month": tokens}},
        )
        # Usage counter record
        month_str = datetime.now(timezone.utc).strftime("%Y-%m")
        await usage_counters_collection.update_one(
            {"company_id": str(company_id), "month": month_str},
            {"$inc": {"queries": 1, "tokens": tokens}},
            upsert=True,
        )

    async def save_conversation(self, conv_doc: Dict[str, Any]) -> str:
        res = await conversations_collection.insert_one(conv_doc)
        return str(res.inserted_id)

    async def save_message(self, msg_doc: Dict[str, Any]) -> str:
        res = await messages_collection.insert_one(msg_doc)
        return str(res.inserted_id)

    async def list_user_conversations(self, user_id: str, company_id: str) -> List[Dict[str, Any]]:
        docs = await conversations_collection.find(
            {"user_id": str(user_id), "company_id": str(company_id)}
        ).sort("created_at", -1).to_list(100)
        return [{"id": str(d["_id"]), "title": d.get("title", "Conversation")} for d in docs]

    async def save_feedback(self, message_id: str, user_id: str, helpful: bool) -> None:
        msg_oid = ObjectId(message_id) if ObjectId.is_valid(message_id) else message_id
        msg = await messages_collection.find_one({"_id": msg_oid, "user_id": str(user_id)})
        if not msg:
            raise NotFoundError("Message not found or does not belong to you.")
        await feedback_collection.insert_one({
            "message_id": str(message_id),
            "user_id": str(user_id),
            "helpful": helpful,
            "created_at": datetime.now(timezone.utc),
        })

    async def get_schema_catalog(self, company_id: str) -> Dict[str, Any]:
        return await self._catalog_repo.get_catalog(company_id)
