"""
Profile Mongo Repository (app/infrastructure/db/profile_repository.py)
Persistence implementation for user profiles and session tracking.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.infrastructure.azure.redis_cache import get_cache_client
from app.infrastructure.db.mongodb import (
    profile_session_state_collection,
    user_profiles_collection,
)
from app.modules.profile.interfaces import ProfileRepositoryProtocol
from app.modules.profile.models import (
    ProfileField,
    ProfileSessionState,
    UserProfile,
)


class MongoProfileRepository(ProfileRepositoryProtocol):
    def __init__(self):
        self._cache = get_cache_client()

    def _cache_key(self, company_id: str, user_id: str) -> str:
        return f"profile:{company_id}:{user_id}"

    async def get_profile(self, company_id: str, user_id: str) -> Optional[UserProfile]:
        cached = await self._cache.get(self._cache_key(company_id, user_id))
        if cached:
            try:
                return UserProfile.model_validate_json(cached)
            except Exception:
                pass
        doc = await user_profiles_collection.find_one({"company_id": company_id, "user_id": user_id}, {"_id": 0})
        if not doc:
            return None
        prof = UserProfile.model_validate(doc)
        await self._cache.set(self._cache_key(company_id, user_id), prof.model_dump_json(), ex=settings.PROFILE_CACHE_TTL_SECONDS)
        return prof

    async def create_or_init_profile(
        self, company_id: str, user_id: str, account_name: Optional[str] = None, account_company_name: Optional[str] = None
    ) -> UserProfile:
        existing = await self.get_profile(company_id, user_id)
        if existing:
            return existing
        now = datetime.now(timezone.utc)
        prefill = settings.PROFILE_PREFILL_FROM_ACCOUNT
        fields: Dict[str, Any] = {
            "name": {"value": account_name if (prefill and account_name) else None, "status": "collected" if (prefill and account_name) else "missing", "source": "account" if (prefill and account_name) else None, "updated_at": now, "ask_count": 0},
            "company_name": {"value": account_company_name if (prefill and account_company_name) else None, "status": "collected" if (prefill and account_company_name) else "missing", "source": "account" if (prefill and account_company_name) else None, "updated_at": now, "ask_count": 0},
            "interest": {"value": [], "status": "missing", "source": None, "updated_at": now, "ask_count": 0},
        }
        doc = {"company_id": company_id, "user_id": user_id, "created_at": now, "updated_at": now, "opted_out": False, "completed": False, "fields": fields}
        try:
            await user_profiles_collection.insert_one(doc)
        except Exception:
            pass
        return await self.get_profile(company_id, user_id) or UserProfile.model_validate(doc)

    async def merge_profile_fields(
        self, company_id: str, user_id: str, validated_fields: Dict[str, Any], source: str, corrections: List[str]
    ) -> UserProfile:
        now = datetime.now(timezone.utc)
        for fld in ["name", "company_name"]:
            if fld in validated_fields and validated_fields[fld]:
                val = validated_fields[fld]
                q = {"company_id": company_id, "user_id": user_id, f"fields.{fld}.source": {"$ne": "account"}} if fld in corrections else {"company_id": company_id, "user_id": user_id, f"fields.{fld}.status": {"$ne": "collected"}}
                await user_profiles_collection.update_one(q, {"$set": {f"fields.{fld}.value": val, f"fields.{fld}.status": "collected", f"fields.{fld}.source": source, f"fields.{fld}.updated_at": now, "updated_at": now}})
        if "interest" in validated_fields and validated_fields["interest"]:
            items = validated_fields["interest"]
            if "interest" in corrections:
                await user_profiles_collection.update_one({"company_id": company_id, "user_id": user_id}, {"$set": {"fields.interest.value": items[:5], "fields.interest.status": "collected", "fields.interest.source": source, "fields.interest.updated_at": now, "updated_at": now}})
            else:
                doc = await user_profiles_collection.find_one({"company_id": company_id, "user_id": user_id}, {"_id": 0})
                cur = (doc or {}).get("fields", {}).get("interest", {}).get("value") or []
                seen = {x.lower() for x in cur if isinstance(x, str)}
                for it in items:
                    if it.lower() not in seen:
                        cur.append(it)
                        seen.add(it.lower())
                await user_profiles_collection.update_one({"company_id": company_id, "user_id": user_id}, {"$set": {"fields.interest.value": cur[-5:], "fields.interest.status": "collected", "fields.interest.source": source, "fields.interest.updated_at": now, "updated_at": now}})
        # Recompute completed
        doc = await user_profiles_collection.find_one({"company_id": company_id, "user_id": user_id}, {"_id": 0})
        if doc:
            req_fields = [f.strip() for f in settings.PROFILE_FIELDS_ORDER.split(",") if f.strip()]
            all_done = all(doc.get("fields", {}).get(f, {}).get("status") in ("collected", "skipped") for f in req_fields)
            if doc.get("completed") != all_done:
                await user_profiles_collection.update_one({"company_id": company_id, "user_id": user_id}, {"$set": {"completed": all_done}})
                doc["completed"] = all_done
        await self._cache.delete(self._cache_key(company_id, user_id))
        return UserProfile.model_validate(doc)

    async def set_field_status(self, company_id: str, user_id: str, field_name: str, status: str) -> None:
        now = datetime.now(timezone.utc)
        await user_profiles_collection.update_one({"company_id": company_id, "user_id": user_id}, {"$set": {f"fields.{field_name}.status": status, f"fields.{field_name}.updated_at": now, "updated_at": now}})
        doc = await user_profiles_collection.find_one({"company_id": company_id, "user_id": user_id}, {"_id": 0})
        if doc:
            req_fields = [f.strip() for f in settings.PROFILE_FIELDS_ORDER.split(",") if f.strip()]
            all_done = all(doc.get("fields", {}).get(f, {}).get("status") in ("collected", "skipped") for f in req_fields)
            await user_profiles_collection.update_one({"company_id": company_id, "user_id": user_id}, {"$set": {"completed": all_done}})
        await self._cache.delete(self._cache_key(company_id, user_id))

    async def set_opted_out(self, company_id: str, user_id: str) -> None:
        now = datetime.now(timezone.utc)
        await user_profiles_collection.update_one({"company_id": company_id, "user_id": user_id}, {"$set": {"opted_out": True, "updated_at": now}})
        await self._cache.delete(self._cache_key(company_id, user_id))

    async def delete_profile_and_states(self, company_id: str, user_id: str) -> bool:
        await user_profiles_collection.delete_one({"company_id": company_id, "user_id": user_id})
        await profile_session_state_collection.delete_many({"company_id": company_id, "user_id": user_id})
        await self._cache.delete(self._cache_key(company_id, user_id))
        return True

    async def record_prompt_shown(self, company_id: str, user_id: str, conversation_id: str, field_name: str) -> None:
        now = datetime.now(timezone.utc)
        await profile_session_state_collection.update_one(
            {"company_id": company_id, "user_id": user_id, "conversation_id": conversation_id},
            {
                "$set": {"pending_field": field_name, "answers_since_last_ask": 0, "last_prompt_at": now, "updated_at": now},
                "$inc": {"asks_in_conversation": 1},
            },
            upsert=True,
        )
        await user_profiles_collection.update_one(
            {"company_id": company_id, "user_id": user_id},
            {"$inc": {f"fields.{field_name}.ask_count": 1}, "$set": {"updated_at": now}},
        )
        await self._cache.delete(self._cache_key(company_id, user_id))

    async def get_session_state(self, company_id: str, user_id: str, conversation_id: str) -> Optional[ProfileSessionState]:
        doc = await profile_session_state_collection.find_one({"company_id": company_id, "user_id": user_id, "conversation_id": conversation_id}, {"_id": 0})
        if doc:
            return ProfileSessionState.model_validate(doc)
        return ProfileSessionState(company_id=company_id, user_id=user_id, conversation_id=conversation_id)

    async def save_session_state(self, state: ProfileSessionState) -> None:
        state.updated_at = datetime.now(timezone.utc)
        await profile_session_state_collection.update_one({"company_id": state.company_id, "user_id": state.user_id, "conversation_id": state.conversation_id}, {"$set": state.model_dump()}, upsert=True)
