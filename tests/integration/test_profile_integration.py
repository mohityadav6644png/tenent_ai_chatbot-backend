"""
Profile Integration Tests (tests/integration/test_profile_integration.py)
Tests multi-turn progressive profiling integration with chat service, session state, and endpoints.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.core.config import settings
from app.infrastructure.azure.redis_cache import InMemoryCache
from app.infrastructure.profile_extractor.fake_extractor import FakeProfileExtractor
from app.modules.chat.models import create_conversation_document, create_message_document
from app.modules.chat.service import QueryService
from app.modules.profile.models import (
    ExtractionResult,
    ProfileField,
    ProfilePrompt,
    ProfileSessionState,
    UserProfile,
)
from app.modules.profile.service import ProfileService


class MockProfileRepo:
    def __init__(self):
        self.profiles = {}
        self.sessions = {}
        self._cache = InMemoryCache()

    def _cache_key(self, company_id: str, user_id: str) -> str:
        return f"profile:{company_id}:{user_id}"

    async def get_profile(self, company_id: str, user_id: str):
        return self.profiles.get((company_id, user_id))

    async def create_or_init_profile(self, company_id: str, user_id: str, account_name=None, account_company_name=None):
        if (company_id, user_id) in self.profiles:
            return self.profiles[(company_id, user_id)]
        now = datetime.now(timezone.utc)
        prefill = settings.PROFILE_PREFILL_FROM_ACCOUNT
        fields = {
            "name": ProfileField(
                value=account_name if (prefill and account_name) else None,
                status="collected" if (prefill and account_name) else "missing",
                source="account" if (prefill and account_name) else None,
                updated_at=now,
            ),
            "company_name": ProfileField(
                value=account_company_name if (prefill and account_company_name) else None,
                status="collected" if (prefill and account_company_name) else "missing",
                source="account" if (prefill and account_company_name) else None,
                updated_at=now,
            ),
            "interest": ProfileField(value=[], status="missing", source=None, updated_at=now),
        }
        prof = UserProfile(
            company_id=company_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            opted_out=False,
            completed=False,
            fields=fields,
        )
        self.profiles[(company_id, user_id)] = prof
        return prof

    async def merge_profile_fields(self, company_id: str, user_id: str, validated_fields, source, corrections):
        prof = await self.get_profile(company_id, user_id)
        if not prof:
            prof = await self.create_or_init_profile(company_id, user_id)
        now = datetime.now(timezone.utc)
        for fld in ["name", "company_name"]:
            if fld in validated_fields and validated_fields[fld]:
                cur_src = prof.fields[fld].source
                if cur_src == "account":
                    continue  # Never replace account source
                if fld in corrections or prof.fields[fld].status != "collected":
                    prof.fields[fld].value = validated_fields[fld]
                    prof.fields[fld].status = "collected"
                    prof.fields[fld].source = source
                    prof.fields[fld].updated_at = now
        if "interest" in validated_fields and validated_fields["interest"]:
            if "interest" in corrections:
                prof.fields["interest"].value = validated_fields["interest"][:5]
            else:
                cur = list(prof.fields["interest"].value or [])
                seen = {x.lower() for x in cur}
                for it in validated_fields["interest"]:
                    if it.lower() not in seen:
                        cur.append(it)
                        seen.add(it.lower())
                prof.fields["interest"].value = cur[-5:]
            prof.fields["interest"].status = "collected"
            prof.fields["interest"].source = source
            prof.fields["interest"].updated_at = now
        prof.updated_at = now
        all_done = all(prof.fields[f].status in ("collected", "skipped") for f in ["name", "company_name", "interest"])
        prof.completed = all_done
        return prof

    async def set_field_status(self, company_id: str, user_id: str, field_name: str, status: str):
        prof = await self.get_profile(company_id, user_id)
        if prof and field_name in prof.fields:
            prof.fields[field_name].status = status

    async def set_opted_out(self, company_id: str, user_id: str):
        prof = await self.get_profile(company_id, user_id)
        if prof:
            prof.opted_out = True

    async def delete_profile_and_states(self, company_id: str, user_id: str):
        self.profiles.pop((company_id, user_id), None)
        keys_to_del = [k for k in self.sessions if k[0] == company_id and k[1] == user_id]
        for k in keys_to_del:
            self.sessions.pop(k, None)
        return True

    async def get_session_state(self, company_id: str, user_id: str, conversation_id: str):
        return self.sessions.get((company_id, user_id, conversation_id))

    async def save_session_state(self, state: ProfileSessionState):
        self.sessions[(state.company_id, state.user_id, state.conversation_id)] = state

    async def record_prompt_shown(self, company_id: str, user_id: str, conversation_id: str, field_name: str):
        st = await self.get_session_state(company_id, user_id, conversation_id)
        if not st:
            st = ProfileSessionState(company_id=company_id, user_id=user_id, conversation_id=conversation_id)
        st.pending_field = field_name
        st.asks_in_conversation += 1
        st.answers_since_last_ask = 0
        st.last_prompt_at = datetime.now(timezone.utc)
        self.sessions[(company_id, user_id, conversation_id)] = st
        prof = await self.get_profile(company_id, user_id)
        if prof and field_name in prof.fields:
            prof.fields[field_name].ask_count += 1


@pytest.fixture
def mock_chat_repo():
    repo = MagicMock()
    repo.check_and_increment_rate_limits = AsyncMock()
    repo.increment_monthly_usage = AsyncMock()
    repo.save_conversation = AsyncMock(return_value="conv-123")
    repo.save_message = AsyncMock(return_value="msg-123")
    repo.get_conversation = AsyncMock(return_value={"id": "conv-123", "company_id": "comp_test", "user_id": "user_test"})
    repo.get_conversation_messages = AsyncMock(return_value=[])
    repo.get_schema_catalog = AsyncMock(
        return_value={
            "schema_version": 1,
            "allowed_collections": ["brightpath_students"],
            "files": [
                {
                    "slug": "brightpath_students",
                    "name": "Students",
                    "columns": [{"key": "class", "type": "text", "hidden": False}],
                }
            ],
            "file_fields": {"class": "string"},
            "total_row_count": 100,
        }
    )
    return repo


@pytest.fixture
def mock_executor():
    executor = MagicMock()
    executor.execute_query = AsyncMock(
        return_value=([{"name": "cnt", "type": "number"}], [[42]], False)
    )
    return executor


@pytest.fixture
def mock_generator():
    generator = MagicMock()
    generator.generate = AsyncMock(
        return_value={
            "decision": "answer",
            "query": {
                "collection": "brightpath_students",
                "pipeline": [{"$project": {"_id": 0, "class": 1}}],
            },
            "headline_template": "Total is 42",
            "detail_template": "Detail text",
            "follow_ups": [],
        }
    )
    return generator


@pytest.mark.asyncio
async def test_progressive_profiling_flow(mock_executor, mock_generator, mock_chat_repo):
    repo = MockProfileRepo()
    extractor = FakeProfileExtractor()
    profile_service = ProfileService(repo=repo, extractor=extractor)
    cache = InMemoryCache()

    query_service = QueryService(
        executor=mock_executor,
        generator=mock_generator,
        repo=mock_chat_repo,
        cache=cache,
        profile_service=profile_service,
    )

    company_id = "comp_test"
    user_id = "user_test"
    conv_id = "conv-123"

    # Turn 1: Analytics question 1 (no prompt yet, needs 2 answers)
    res1 = await query_service.answer(company_id, user_id, "Show count", [], conversation_id=conv_id)
    assert res1["status"] == "success"
    assert res1["profile_prompt"] is None

    # Turn 2: Analytics question 2 -> Reaches min 2 answers, prompts for name!
    res2 = await query_service.answer(company_id, user_id, "Show count again", [], conversation_id=conv_id)
    assert res2["status"] == "success"
    assert res2["profile_prompt"] is not None
    assert res2["profile_prompt"].field == "name"

    # Turn 3: User answers prompt: "I'm Mohit" -> Handled and acknowledged without analytics call!
    res3 = await query_service.answer(company_id, user_id, "I'm Mohit", [], conversation_id=conv_id)
    assert res3["status"] == "success"
    assert "Mohit" in res3["answer"]["headline"]
    assert res3["answer"]["rows"] == []  # No analytics table
    assert res3["profile_prompt"] is None

    # Verify profile stored
    prof = await repo.get_profile(company_id, user_id)
    assert prof.fields["name"].value == "Mohit"
    assert prof.fields["name"].status == "collected"

    # Turn 4: User sends message with both profile answer and question: "interested in Sales, count students"
    # Normal pipeline answers question
    res4 = await query_service.answer(company_id, user_id, "interested in Sales, count students", [], conversation_id=conv_id)
    assert res4["status"] == "success"
    assert res4["answer"]["headline"] == "Total is 42"
    await asyncio.sleep(0.05)

    prof = await repo.get_profile(company_id, user_id)
    assert "Sales" in prof.fields["interest"].value

    # Turn 5: Skip behavior: when prompt pending, sending skip phrase marks skipped
    st = await repo.get_session_state(company_id, user_id, conv_id)
    st.pending_field = "company_name"
    await repo.save_session_state(st)

    res5 = await query_service.answer(company_id, user_id, "baad mein", [], conversation_id=conv_id)
    assert res5["status"] == "success"
    assert "skipped" in res5["answer"]["headline"].lower()
    prof = await repo.get_profile(company_id, user_id)
    assert prof.fields["company_name"].status == "skipped"

    # Turn 6: Opt out: sending opt-out phrase stops all future prompts
    st.pending_field = "interest"
    await repo.save_session_state(st)
    res6 = await query_service.answer(company_id, user_id, "don't ask", [], conversation_id=conv_id)
    assert "won't ask" in res6["answer"]["headline"]
    prof = await repo.get_profile(company_id, user_id)
    assert prof.opted_out is True


@pytest.mark.asyncio
async def test_extractor_failure_graceful_fallback(mock_executor, mock_generator, mock_chat_repo):
    repo = MockProfileRepo()
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=asyncio.TimeoutError("timeout"))
    profile_service = ProfileService(repo=repo, extractor=extractor)
    cache = InMemoryCache()

    query_service = QueryService(
        executor=mock_executor,
        generator=mock_generator,
        repo=mock_chat_repo,
        cache=cache,
        profile_service=profile_service,
    )

    # When extractor times out, query service doesn't crash
    res = await query_service.answer("comp_test", "user_test", "Show count", [], conversation_id="conv-123")
    assert res["status"] == "success"
