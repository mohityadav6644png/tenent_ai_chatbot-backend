"""
Profile Interfaces (app/modules/profile/interfaces.py)
Protocol interfaces for profile repositories, services, and extractors.
Never imports motor, redis, google-genai, or fastapi.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol
from app.modules.profile.models import (
    ExtractionResult,
    ProfileFieldName,
    ProfilePrompt,
    ProfileSessionState,
    UserProfile,
)


class ProfileRepositoryProtocol(Protocol):
    """Protocol for profile persistence operations scoped by company_id."""

    async def get_profile(self, company_id: str, user_id: str) -> Optional[UserProfile]:
        ...

    async def create_or_init_profile(
        self,
        company_id: str,
        user_id: str,
        account_name: Optional[str] = None,
        account_company_name: Optional[str] = None,
    ) -> UserProfile:
        ...

    async def merge_profile_fields(
        self,
        company_id: str,
        user_id: str,
        validated_fields: Dict[str, Any],
        source: str,
        corrections: List[str],
    ) -> UserProfile:
        ...

    async def set_field_status(
        self,
        company_id: str,
        user_id: str,
        field_name: str,
        status: str,
    ) -> None:
        ...

    async def set_opted_out(self, company_id: str, user_id: str) -> None:
        ...

    async def delete_profile_and_states(self, company_id: str, user_id: str) -> bool:
        ...

    async def get_session_state(
        self, company_id: str, user_id: str, conversation_id: str
    ) -> Optional[ProfileSessionState]:
        ...

    async def save_session_state(self, state: ProfileSessionState) -> None:
        ...


class ProfileExtractor(Protocol):
    """Protocol for LLM-based profile attribute extraction."""

    async def extract(
        self,
        message: str,
        allowed_fields: List[str],
        pending_field: Optional[str] = None,
    ) -> ExtractionResult:
        ...
