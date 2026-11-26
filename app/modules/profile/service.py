"""
Profile Service (app/modules/profile/service.py)
Orchestrates profile loading, extraction gating, skip/opt-out handling, and prompts.
Never imports motor, redis, google-genai, or fastapi.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from app.core.config import settings
from app.core.logging import logger
from app.modules.profile.interfaces import ProfileExtractor, ProfileRepositoryProtocol
from app.modules.profile.models import (
    ExtractionResult,
    ProfilePrompt,
    ProfileSessionState,
    UserProfile,
)
from app.modules.profile.policy import decide_prompt
from app.modules.profile.sanitizer import (
    matches_phrase,
    sanitize_company_name,
    sanitize_interests,
    sanitize_name,
)

TRIGGER_PATTERNS = ["my name is", "i am", "i'm", "im ", "i work", "from ", "interested in", "looking for", "call me"]


def _validate_extracted(result: ExtractionResult) -> Dict[str, Any]:
    v: Dict[str, Any] = {}
    if result.name and (n := sanitize_name(result.name)):
        v["name"] = n
    if result.company_name and (c := sanitize_company_name(result.company_name)):
        v["company_name"] = c
    if result.interest and (i := sanitize_interests(result.interest)):
        v["interest"] = i
    return v


class ProfileService:
    def __init__(
        self,
        repo: ProfileRepositoryProtocol,
        extractor: ProfileExtractor,
        user_repo: Optional[Any] = None,
        tenant_repo: Optional[Any] = None,
    ):
        self.repo = repo
        self.extractor = extractor
        self.user_repo = user_repo
        self.tenant_repo = tenant_repo

    async def get_or_init_profile(self, company_id: str, user_id: str) -> UserProfile:
        prof = await self.repo.get_profile(company_id, user_id)
        if prof:
            return prof
        account_name, account_company = None, None
        if settings.PROFILE_PREFILL_FROM_ACCOUNT:
            if self.user_repo and hasattr(self.user_repo, "get_user_by_id"):
                try:
                    u = await self.user_repo.get_user_by_id(user_id)
                    account_name = sanitize_name(u.get("name")) if u else None
                except Exception:
                    pass
            if self.tenant_repo and hasattr(self.tenant_repo, "get_tenant_by_id"):
                try:
                    t = await self.tenant_repo.get_tenant_by_id(company_id)
                    account_company = sanitize_company_name(t.get("name")) if t else None
                except Exception:
                    pass
        return await self.repo.create_or_init_profile(company_id, user_id, account_name, account_company)

    async def get_session_state(self, company_id: str, user_id: str, conversation_id: str) -> ProfileSessionState:
        return await self.repo.get_session_state(company_id, user_id, conversation_id) or ProfileSessionState(
            company_id=company_id, user_id=user_id, conversation_id=conversation_id
        )

    def matches_trigger(self, message: str) -> bool:
        low = (message or "").lower()
        return any(p in low for p in TRIGGER_PATTERNS)

    async def handle_pending_turn(
        self, company_id: str, user_id: str, conversation_id: str, message: str, profile: UserProfile, state: ProfileSessionState
    ) -> Tuple[Optional[str], Optional[str]]:
        pending = state.pending_field
        if not pending:
            return None, None

        if matches_phrase(message, settings.PROFILE_OPT_OUT_PHRASES):
            await self.repo.set_opted_out(company_id, user_id)
            state.pending_field = None
            await self.repo.save_session_state(state)
            return "Understood, I won't ask you again.", None

        if matches_phrase(message, settings.PROFILE_SKIP_PHRASES):
            await self.repo.set_field_status(company_id, user_id, pending, "skipped")
            state.pending_field = None
            await self.repo.save_session_state(state)
            return "No problem, skipped.", None

        allowed = [f.strip() for f in settings.PROFILE_FIELDS_ORDER.split(",") if f.strip()]
        result = await self.extractor.extract(message, allowed, pending_field=pending)
        validated = _validate_extracted(result)

        state.pending_field = None
        await self.repo.save_session_state(state)

        if validated:
            updated_prof = await self.repo.merge_profile_fields(company_id, user_id, validated, "user_message", result.corrections)
            if result.message_kind == "profile_answer":
                known_name = updated_prof.fields.get("name", {}).value if updated_prof.fields.get("name") else None
                headline = f"Thanks {known_name}, noted." if (known_name and isinstance(known_name, str)) else "Thanks, noted."
                return headline, None
            elif result.message_kind == "both":
                return None, result.remaining_question or message

        pf = profile.fields.get(pending)
        if pf and (pf.ask_count >= settings.PROFILE_MAX_ASKS_PER_FIELD):
            await self.repo.set_field_status(company_id, user_id, pending, "skipped")

        return None, None

    async def extract_and_merge(
        self, company_id: str, user_id: str, message: str, pending_field: Optional[str] = None
    ) -> Tuple[Optional[ExtractionResult], UserProfile, Dict[str, Any]]:
        allowed = [f.strip() for f in settings.PROFILE_FIELDS_ORDER.split(",") if f.strip()]
        result = await self.extractor.extract(message, allowed, pending_field=pending_field)
        validated = _validate_extracted(result)
        prof = await self.repo.get_profile(company_id, user_id)
        if not prof:
            prof = await self.get_or_init_profile(company_id, user_id)
        if validated:
            prof = await self.repo.merge_profile_fields(company_id, user_id, validated, "user_message", result.corrections)
        return result, prof, validated

    async def extract_background(self, company_id: str, user_id: str, message: str) -> None:
        try:
            if not getattr(settings, "PROFILE_ENABLED", True):
                return
            await self.extract_and_merge(company_id, user_id, message)
        except Exception as exc:
            logger.warning(f"bg_extraction_error: {type(exc).__name__}")

    async def post_turn_prompt(
        self, company_id: str, user_id: str, conversation_id: str, profile: UserProfile, is_success: bool
    ) -> Optional[ProfilePrompt]:
        state = await self.get_session_state(company_id, user_id, conversation_id)
        if is_success:
            state.successful_answers += 1
            state.answers_since_last_ask += 1

        prompt = decide_prompt(profile, state, is_success)
        if prompt:
            await self.repo.record_prompt_shown(company_id, user_id, conversation_id, prompt.field)
        else:
            await self.repo.save_session_state(state)
        return prompt
