"""
Profile Models (app/modules/profile/models.py)
Domain models, enums, and schemas for conversational progressive user profiling.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field

ProfileFieldName = Literal["name", "company_name", "interest"]
FieldStatus = Literal["missing", "collected", "skipped"]
FieldSource = Literal["account", "user_message", "user_edit"]
MessageKind = Literal["profile_answer", "question", "both", "other"]


class ProfilePrompt(BaseModel):
    """Prompt asking the user for one missing profile attribute."""
    field: ProfileFieldName
    text: str
    skippable: bool = True


class ExtractionResult(BaseModel):
    """Extracted profile attributes from user input."""
    name: Optional[str] = None
    company_name: Optional[str] = None
    interest: List[str] = Field(default_factory=list)
    corrections: List[ProfileFieldName] = Field(default_factory=list)
    message_kind: MessageKind = "other"
    remaining_question: Optional[str] = None


class ProfileField(BaseModel):
    """Individual profile field state and tracking."""
    value: Optional[Union[str, List[str]]] = None
    status: FieldStatus = "missing"
    source: Optional[FieldSource] = None
    updated_at: Optional[datetime] = None
    ask_count: int = 0


class UserProfile(BaseModel):
    """Durable per-user profile document."""
    company_id: str
    user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    opted_out: bool = False
    completed: bool = False
    fields: Dict[str, ProfileField] = Field(default_factory=dict)


class ProfileSessionState(BaseModel):
    """Conversation-level tracking state for progressive ask timing."""
    company_id: str
    user_id: str
    conversation_id: str
    successful_answers: int = 0
    answers_since_last_ask: int = 0
    asks_in_conversation: int = 0
    pending_field: Optional[str] = None
    last_prompt_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
