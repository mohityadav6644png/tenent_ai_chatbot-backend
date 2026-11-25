"""
Follow-up Resolution Service (app/modules/chat/followup.py)
Domain service for rewriting conversational follow-ups into complete standalone questions.
Enforces strict skipping rules, untrusted text boundaries, timeouts, and output validation.
"""

import asyncio
import re
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.logging import logger
from app.modules.chat.interfaces import FollowUpResolver


def is_clearly_independent(new_message: str, catalog: Dict[str, Any]) -> bool:
    """Returns True if message is clearly an independent question rather than a conversational follow-up."""
    words = new_message.strip().split()
    if not words:
        return False

    q_lower = new_message.lower().strip()

    # Collect known collection names, file slugs/names, and column names
    known_tokens = set()
    allowed_collections = catalog.get("allowed_collections") or catalog.get("allowed_views") or []
    for c in allowed_collections:
        c_str = str(c).lower()
        known_tokens.add(c_str)
        known_tokens.add(c_str.replace("_", " "))

    files = catalog.get("files") or []
    for f in files:
        if f.get("slug"):
            s_str = str(f["slug"]).lower()
            known_tokens.add(s_str)
            known_tokens.add(s_str.replace("_", " "))
        if f.get("name"):
            known_tokens.add(str(f["name"]).lower())
        for col in f.get("columns", []):
            if not col.get("hidden") and col.get("key"):
                k_str = str(col["key"]).lower()
                known_tokens.add(k_str)
                known_tokens.add(k_str.replace("_", " "))
                for part in k_str.split("_"):
                    if len(part) > 2:
                        known_tokens.add(part)
            if not col.get("hidden") and col.get("label"):
                known_tokens.add(str(col["label"]).lower())

    file_fields = catalog.get("file_fields") or {}
    for col_name in file_fields.keys():
        k_str = str(col_name).lower()
        known_tokens.add(k_str)
        known_tokens.add(k_str.replace("_", " "))

    has_known_token = any(
        len(token) > 1 and re.search(r"\b" + re.escape(token) + r"\b", q_lower)
        for token in known_tokens
    )

    # Explicit gate rules:
    # 1. Call the resolver when the message starts with "and", "also", "only", "now", "same", "what about", "how about", "which of"
    call_starters = ("and", "also", "only", "now", "same", "what about", "how about", "which of")
    if any(q_lower.startswith(cs) for cs in call_starters):
        return False

    # 2. Call the resolver when message has at most 6 words and contains a reference word
    ref_words = {"it", "its", "that", "those", "them", "this", "these", "they", "their"}
    if len(words) <= 6 and any(w in ref_words for w in re.findall(r"\b\w+\b", q_lower)):
        return False

    # 3. Treat the message as independent when it has more than 12 words and names a file or column
    if len(words) > 12 and has_known_token:
        return True

    if not has_known_token:
        return False

    # Standalone complete question check: starts with an interrogative/command
    # and has NO anaphoric pronouns or follow-up continuation markers
    question_starters = (
        "which", "what", "how many", "how much", "who", "show", "list", "tell me",
        "find", "give me", "count", "total", "average", "where", "when"
    )
    starts_with_question = any(q_lower.startswith(qs) for qs in question_starters)

    followup_cues = (
        "instead", "what about", "how about", "and for", "and by", "only ", "just ",
        "that", "those", "these", "them", "him", "her", "above", "previous", "same", "last"
    )
    has_followup_cue = any(re.search(r"\b" + re.escape(cue.strip()) + r"\b", q_lower) for cue in followup_cues)

    if starts_with_question and not has_followup_cue:
        return True

    return False


def validate_resolver_output(res: Any, original_message: str) -> Optional[Dict[str, Any]]:
    """Validates structured resolver output against length and markup safety constraints."""
    if not isinstance(res, dict):
        return None

    is_followup = bool(res.get("is_followup", False))
    needs_clarification = bool(res.get("needs_clarification", False))
    clarification_question = res.get("clarification_question")
    if clarification_question is not None and not isinstance(clarification_question, str):
        clarification_question = None

    standalone_question = res.get("standalone_question")
    if not isinstance(standalone_question, str) or not standalone_question.strip():
        standalone_question = original_message

    standalone_question = standalone_question.strip()

    # Length constraint
    if len(standalone_question) > settings.FOLLOWUP_MAX_QUESTION_CHARS:
        return None

    # Markup or code rejection
    if re.search(r"<[^>]+>", standalone_question) or "```" in standalone_question:
        return None

    return {
        "is_followup": is_followup,
        "standalone_question": standalone_question,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question.strip() if clarification_question else None,
    }


def build_sanitized_turn_context(turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extracts strictly safe metadata for the previous turns: no rows, headlines, or hidden columns."""
    sanitized_history = []
    for t in turns:
        q_text = t.get("resolved_question") or t.get("question") or ""
        slug = t.get("file_slug")
        cols = t.get("output_columns") or []
        sanitized_history.append({
            "resolved_question": q_text,
            "file_slug": slug,
            "output_columns": [str(c) for c in cols],
        })

    last_turn = turns[-1] if turns else {}
    last_was_clarification = (last_turn.get("status") == "clarification_needed")
    clar_q = last_turn.get("answer", {}).get("clarification_question") if last_was_clarification else None
    clar_opts = last_turn.get("answer", {}).get("options", []) if last_was_clarification else []

    return {
        "turns": sanitized_history,
        "last_was_clarification": last_was_clarification,
        "clarification_question": clar_q,
        "clarification_options": clar_opts,
    }


class FollowUpService:
    def __init__(self, resolver: Optional[FollowUpResolver] = None):
        self.resolver = resolver

    async def resolve(
        self,
        new_message: str,
        turns: List[Dict[str, Any]],
        catalog: Dict[str, Any],
        company_id: str,
    ) -> Dict[str, Any]:
        """Resolves new_message to a standalone question using previous turns, or falls back to original."""
        original = new_message.strip()
        fallback_result = {
            "is_followup": False,
            "standalone_question": original,
            "needs_clarification": False,
            "clarification_question": None,
        }

        # Skip checks
        if not settings.FOLLOWUP_ENABLED:
            return fallback_result

        if not turns:
            return fallback_result

        if is_clearly_independent(original, catalog):
            return fallback_result

        if not self.resolver:
            return fallback_result

        sanitized_context = build_sanitized_turn_context(turns)

        try:
            raw_output = await asyncio.wait_for(
                self.resolver.resolve(
                    new_message=original,
                    turns=sanitized_context["turns"],
                    catalog=catalog,
                    company_id=company_id,
                ),
                timeout=settings.FOLLOWUP_TIMEOUT_SECONDS,
            )
            validated = validate_resolver_output(raw_output, original)
            if validated is None:
                logger.warning("followup_resolver_failed reason=INVALID_OUTPUT")
                return fallback_result
            return validated

        except asyncio.TimeoutError:
            logger.warning("followup_resolver_failed reason=TIMEOUT")
            return fallback_result
        except Exception as exc:
            logger.warning("followup_resolver_failed reason=PROVIDER_ERROR detail=%s", str(exc)[:100])
            return fallback_result
