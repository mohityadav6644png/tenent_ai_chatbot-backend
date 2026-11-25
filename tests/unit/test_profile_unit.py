"""
Profile Unit Tests (tests/unit/test_profile_unit.py)
Table-driven unit tests for profile sanitizer, policy, merge rules, and phrase matching.
"""

from datetime import datetime, timezone
import pytest
from app.core.config import settings
from app.modules.profile.models import (
    ExtractionResult,
    ProfileField,
    ProfilePrompt,
    ProfileSessionState,
    UserProfile,
)
from app.modules.profile.policy import decide_prompt
from app.modules.profile.sanitizer import (
    contains_suspicious_patterns,
    matches_phrase,
    normalize_text,
    sanitize_company_name,
    sanitize_interests,
    sanitize_name,
)


class TestProfileSanitizer:
    def test_normalize_text(self):
        assert normalize_text("  hello   world  ") == "hello world"
        assert normalize_text("test\x00\x08char") == "testchar"

    def test_contains_suspicious_patterns(self):
        assert contains_suspicious_patterns("user@example.com") is True
        assert contains_suspicious_patterns("https://evil.com") is True
        assert contains_suspicious_patterns("www.evil.com") is True
        assert contains_suspicious_patterns("<script>alert(1)</script>") is True
        assert contains_suspicious_patterns("{payload: 1}") is True
        assert contains_suspicious_patterns("Call 9876543210 now") is True  # >= 7 digits
        assert contains_suspicious_patterns("Regular Name") is False

    def test_sanitize_name_valid(self):
        assert sanitize_name("Mohit Yadav") == "Mohit Yadav"
        assert sanitize_name("Jean-Luc O'Connor") == "Jean-Luc O'Connor"
        assert sanitize_name("Dr. Smith") == "Dr. Smith"
        assert sanitize_name("François") == "François"

    def test_sanitize_name_rejected(self):
        assert sanitize_name("A") is None  # Too short (<2)
        assert sanitize_name("A" * 85) is None  # Too long (>80)
        assert sanitize_name("Mohit 123") is None  # Contains digits
        assert sanitize_name("mohit@gmail.com") is None  # Contains @/email
        assert sanitize_name("<Name>") is None  # Markup
        assert sanitize_name(None) is None

    def test_sanitize_company_name_valid(self):
        assert sanitize_company_name("Acme Corp") == "Acme Corp"
        assert sanitize_company_name("AT&T Inc.") == "AT&T Inc."
        assert sanitize_company_name("Smith & Sons, Ltd. (Global)") == "Smith & Sons, Ltd. (Global)"
        assert sanitize_company_name("3M India") == "3M India"  # Digits allowed

    def test_sanitize_company_name_rejected(self):
        assert sanitize_company_name("A") is None
        assert sanitize_company_name("B" * 125) is None
        assert sanitize_company_name("Acme <script>") is None
        assert sanitize_company_name("http://acme.com") is None
        assert sanitize_company_name(None) is None

    def test_sanitize_interests(self):
        raw = ["Sales", "sales", " REVENUE ", "Customer Churn", "a", "x" * 70, "bad@email.com", "Growth"]
        clean = sanitize_interests(raw)
        # Deduplicated case-insensitively, dropped invalid (<2, >60, suspicious), capped at 5
        assert clean == ["Sales", "REVENUE", "Customer Churn", "Growth"]

    def test_matches_phrase(self):
        skip_phrases = ["skip", "not now", "no thanks", "later", "pass", "baad mein", "rehne do"]
        opt_out_phrases = ["dont ask", "don't ask", "stop asking", "mat poochho", "mat poocho"]

        # English skips
        assert matches_phrase("skip", skip_phrases) is True
        assert matches_phrase("SKIP please", skip_phrases) is True
        assert matches_phrase("not now", skip_phrases) is True
        assert matches_phrase("No thanks!", skip_phrases) is False  # punctuation attached without space

        # Hinglish skips
        assert matches_phrase("baad mein", skip_phrases) is True
        assert matches_phrase("rehne do bhai", skip_phrases) is True

        # Opt-out
        assert matches_phrase("don't ask", opt_out_phrases) is True
        assert matches_phrase("stop asking me", opt_out_phrases) is True
        assert matches_phrase("mat poochho", opt_out_phrases) is True
        assert matches_phrase("mat poocho please", opt_out_phrases) is True

        # Non-matching
        assert matches_phrase("Show me top products", skip_phrases) is False
        assert matches_phrase("Who is our best customer?", opt_out_phrases) is False


class TestProfilePolicy:
    def _create_profile(self, name_status="missing", comp_status="missing", int_status="missing", opted_out=False):
        now = datetime.now(timezone.utc)
        return UserProfile(
            company_id="comp-1",
            user_id="user-1",
            opted_out=opted_out,
            completed=False,
            fields={
                "name": ProfileField(status=name_status, ask_count=0, updated_at=now),
                "company_name": ProfileField(status=comp_status, ask_count=0, updated_at=now),
                "interest": ProfileField(status=int_status, ask_count=0, updated_at=now),
            },
        )

    def test_first_ask_timing(self):
        prof = self._create_profile()
        state = ProfileSessionState(
            company_id="comp-1", user_id="user-1", conversation_id="conv-1",
            successful_answers=1, answers_since_last_ask=1, asks_in_conversation=0
        )
        # Less than 2 answers -> None
        assert decide_prompt(prof, state, turn="success") is None

        # Reached 2 answers -> Prompts first field ("name")
        state.successful_answers = 2
        p = decide_prompt(prof, state, turn="success")
        assert p is not None
        assert p.field == "name"

    def test_subsequent_ask_timing(self):
        prof = self._create_profile(name_status="collected")
        state = ProfileSessionState(
            company_id="comp-1", user_id="user-1", conversation_id="conv-1",
            successful_answers=3, answers_since_last_ask=2, asks_in_conversation=1
        )
        # answers_since_last_ask is 2 (< 3) -> None
        assert decide_prompt(prof, state, turn="success") is None

        # answers_since_last_ask is 3 (>= 3) -> Prompts next field ("company_name")
        state.answers_since_last_ask = 3
        p = decide_prompt(prof, state, turn="success")
        assert p is not None
        assert p.field == "company_name"

    def test_never_ask_after_failure_or_clarification(self):
        prof = self._create_profile()
        state = ProfileSessionState(
            company_id="comp-1", user_id="user-1", conversation_id="conv-1",
            successful_answers=5, answers_since_last_ask=5, asks_in_conversation=0
        )
        assert decide_prompt(prof, state, turn="refused") is None
        assert decide_prompt(prof, state, turn="error") is None
        assert decide_prompt(prof, state, turn="clarification_needed") is None
        assert decide_prompt(prof, state, turn={"status": "refused"}) is None

    def test_caps_and_opt_out(self):
        prof = self._create_profile(opted_out=True)
        state = ProfileSessionState(
            company_id="comp-1", user_id="user-1", conversation_id="conv-1",
            successful_answers=10, answers_since_last_ask=5, asks_in_conversation=0
        )
        # Opted out -> None
        assert decide_prompt(prof, state, turn="success") is None

        # Conversation cap
        prof.opted_out = False
        state.asks_in_conversation = settings.PROFILE_MAX_ASKS_PER_CONVERSATION
        assert decide_prompt(prof, state, turn="success") is None

        # Field ask count cap
        state.asks_in_conversation = 1
        prof.fields["name"].ask_count = settings.PROFILE_MAX_ASKS_PER_FIELD
        p = decide_prompt(prof, state, turn="success")
        # Skips "name" and picks "company_name"
        assert p is not None
        assert p.field == "company_name"

    def test_all_fields_collected_or_skipped(self):
        prof = self._create_profile(name_status="collected", comp_status="skipped", int_status="collected")
        state = ProfileSessionState(
            company_id="comp-1", user_id="user-1", conversation_id="conv-1",
            successful_answers=10, answers_since_last_ask=5, asks_in_conversation=0
        )
        assert decide_prompt(prof, state, turn="success") is None
