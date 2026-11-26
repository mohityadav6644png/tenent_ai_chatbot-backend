"""
Fake Profile Extractor (app/infrastructure/profile_extractor/fake_extractor.py)
Deterministic test extractor implementing ProfileExtractor protocol.
"""

import re
from typing import List, Optional
from app.modules.profile.interfaces import ProfileExtractor
from app.modules.profile.models import ExtractionResult


class FakeProfileExtractor(ProfileExtractor):
    """Deterministic profile extractor for unit and integration testing."""

    async def extract(
        self,
        message: str,
        allowed_fields: List[str],
        pending_field: Optional[str] = None,
    ) -> ExtractionResult:
        msg = message.strip()
        low = msg.lower()

        name = None
        company_name = None
        interests: List[str] = []
        corrections: List[str] = []
        remaining_question = None

        # Check for both (e.g. "I'm Mohit, show me total sales")
        split_match = re.split(r"[,;]\s*(?=show|what|how|which|can|tell|list|count|\?)", msg, flags=re.IGNORECASE)
        profile_part = split_match[0].strip()
        question_part = split_match[1].strip() if len(split_match) > 1 else None

        # Name patterns
        m_name = re.search(
            r"(?:my name is|i am|i'm|call me)\s+([A-Za-z.'-]+(?:\s+[A-Za-z.'-]+)*?)(?:\s+(?:from|at|work at|with)\b|$)",
            profile_part,
            re.IGNORECASE,
        )
        if m_name and "name" in allowed_fields:
            name = m_name.group(1).strip()
        elif pending_field == "name" and len(msg.split()) <= 4 and "?" not in msg:
            # Direct answer to pending name prompt
            name = msg.strip()

        # Company patterns
        m_comp = re.search(r"(?:work at|company is|from|with)\s+([A-Za-z0-9\s&.',()-]+)", profile_part, re.IGNORECASE)
        if m_comp and "company_name" in allowed_fields:
            company_name = m_comp.group(1).strip()
        elif pending_field == "company_name" and "?" not in msg and len(msg.split()) <= 6:
            company_name = msg.strip()

        if name and company_name:
            name = re.sub(rf"\s+(?:from|at|work at|with)\s+{re.escape(company_name)}$", "", name, flags=re.IGNORECASE).strip()

        # Interest patterns
        m_int = re.search(r"(?:interested in|looking for)\s+([A-Za-z0-9\s,]+)", profile_part, re.IGNORECASE)
        if m_int and "interest" in allowed_fields:
            interests = [i.strip() for i in m_int.group(1).split(",") if i.strip()]
        elif pending_field == "interest" and "?" not in msg:
            interests = [i.strip() for i in msg.split(",") if i.strip()]

        if name:
            corrections.append("name")
        if company_name:
            corrections.append("company_name")
        if "correction" in low or "actually" in low:
            if interests:
                corrections.append("interest")

        has_profile = bool(name or company_name or interests)
        if has_profile and question_part:
            return ExtractionResult(
                name=name,
                company_name=company_name,
                interest=interests,
                corrections=corrections,
                message_kind="both",
                remaining_question=question_part,
            )
        elif has_profile:
            return ExtractionResult(
                name=name,
                company_name=company_name,
                interest=interests,
                corrections=corrections,
                message_kind="profile_answer",
                remaining_question=None,
            )
        elif "?" in msg or any(w in low for w in ["what", "how", "show", "count", "list"]):
            return ExtractionResult(message_kind="question", remaining_question=msg)
        else:
            return ExtractionResult(message_kind="other", remaining_question=None)
