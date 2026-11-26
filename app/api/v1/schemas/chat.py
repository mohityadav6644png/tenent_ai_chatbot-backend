"""
Chat Schemas (app/api/v1/schemas/chat.py)
Request and response models for chat queries, conversations, and feedback.
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from app.modules.profile.models import ProfilePrompt
from app.modules.multidoc.models import MultiDocAnswerMeta


class ChatHistoryItem(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    history: List[ChatHistoryItem] = Field(default_factory=list)
    conversation_id: Optional[str] = None


class ChatAnswerPayload(BaseModel):
    headline: Optional[str] = None
    detail: Optional[str] = None
    summary: Optional[str] = None
    columns: List[Dict[str, str]] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    chart_hint: Literal["none", "line", "bar"] = "none"
    clarification_question: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    refusal_reason: Optional[str] = None
    parts: Optional[List[Dict[str, Any]]] = None


class ChatMetaPayload(BaseModel):
    latency_ms: int
    cache: Literal["hit", "miss"]
    source: Optional[str] = None
    interpreted_question: Optional[str] = None
    files_used: Optional[List[str]] = None
    timings_ms: Optional[Dict[str, float]] = None
    tokens: Optional[Dict[str, Any]] = None
    catalog_chars: Optional[int] = None


class ChatErrorPayload(BaseModel):
    code: str
    message: str
    retryable: bool = False
    retry_after: Optional[int] = None


class ChatQueryResponse(BaseModel):
    request_id: str
    status: Literal["success", "clarification_needed", "refused", "error"]
    answer: ChatAnswerPayload
    follow_ups: List[str] = Field(default_factory=list)
    meta: ChatMetaPayload
    error: Optional[ChatErrorPayload] = None
    conversation_id: Optional[str] = None
    profile_prompt: Optional[ProfilePrompt] = None
    multidoc_meta: Optional[MultiDocAnswerMeta] = None
    refusal_reason: Optional[str] = None


class ConversationItem(BaseModel):
    id: str
    title: str


class ConversationsListResponse(BaseModel):
    conversations: List[ConversationItem]
