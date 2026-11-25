"""
Profile Policy (app/modules/profile/policy.py)
Pure function ask policy for deciding progressive profiling prompts.
Never imports motor, redis, google-genai, or fastapi.
"""

from typing import Any, Dict, Optional
from app.core.config import settings
from app.modules.profile.models import (
    ProfilePrompt,
    ProfileSessionState,
    UserProfile,
)

PROMPT_TEMPLATES: Dict[str, str] = {
    "name": "By the way, what should I call you? (optional)",
    "company_name": "Which company are you asking for? (optional)",
    "interest": "What are you mostly looking for in your data? (optional)",
}


def _is_turn_successful(turn: Any) -> bool:
    if isinstance(turn, bool):
        return turn
    if isinstance(turn, str):
        return turn.lower() == "success"
    if isinstance(turn, dict):
        status = turn.get("status")
        if status:
            return str(status).lower() == "success"
        return turn.get("is_success") is True
    if hasattr(turn, "status"):
        return str(getattr(turn, "status")).lower() == "success"
    return False


def decide_prompt(
    profile: UserProfile,
    state: ProfileSessionState,
    turn: Any,
) -> Optional[ProfilePrompt]:
    """
    Pure ask policy: returns at most ONE ProfilePrompt for the first missing field in PROFILE_FIELDS_ORDER.
    Only triggered when:
    - PROFILE_ENABLED is true
    - user is not opted_out
    - turn produced a successful analytics answer
    - asks_in_conversation < PROFILE_MAX_ASKS_PER_CONVERSATION
    - first ask: successful_answers >= PROFILE_MIN_ANSWERS_BEFORE_FIRST_ASK
      subsequent asks: answers_since_last_ask >= PROFILE_ASK_EVERY_N_ANSWERS
    - field ask_count < PROFILE_MAX_ASKS_PER_FIELD
    """
    if not settings.PROFILE_ENABLED:
        return None

    if profile.opted_out:
        return None

    if not _is_turn_successful(turn):
        return None

    if state.asks_in_conversation >= settings.PROFILE_MAX_ASKS_PER_CONVERSATION:
        return None

    if state.asks_in_conversation == 0:
        if state.successful_answers < settings.PROFILE_MIN_ANSWERS_BEFORE_FIRST_ASK:
            return None
    else:
        if state.answers_since_last_ask < settings.PROFILE_ASK_EVERY_N_ANSWERS:
            return None

    order = [f.strip() for f in settings.PROFILE_FIELDS_ORDER.split(",") if f.strip()]
    for field_name in order:
        if field_name not in PROMPT_TEMPLATES:
            continue
        field = profile.fields.get(field_name)
        status = field.status if field else "missing"
        ask_count = field.ask_count if field else 0

        if status == "missing" and ask_count < settings.PROFILE_MAX_ASKS_PER_FIELD:
            return ProfilePrompt(
                field=field_name,  # type: ignore[arg-type]
                text=PROMPT_TEMPLATES[field_name],
                skippable=True,
            )

    return None
