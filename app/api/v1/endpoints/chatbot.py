"""
Chatbot Endpoints (app/api/v1/endpoints/chatbot.py)
Provides /chatbot/ask with two-tier tenant-isolated caching (L1 in-memory + L2 Redis).
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_tenant_context
from app.container import get_query_service
from app.core.cache import cache_get, cache_set, hash_question
from app.core.context import TenantContext
from app.modules.chat.service import QueryService

router = APIRouter(tags=["Chatbot"])


class ChatbotAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, description="Natural language question")
    conversation_id: Optional[str] = Field(None, description="Optional existing conversation ID")
    history: List[Dict[str, Any]] = Field(default_factory=list, description="Prior conversation messages")


@router.post("/ask", status_code=status.HTTP_200_OK)
async def ask_chatbot(
    payload: ChatbotAskRequest,
    request: Request,
    current_user: Optional[dict] = Depends(get_current_user),
    service: QueryService = Depends(get_query_service),
):
    """
    NLP Question Answering with Two-Tier Tenant Caching:
    1. Extract company_id from authenticated user's JWT.
    2. Normalize & hash the question to build a tenant-scoped cache key (namespace='chat_answer').
    3. Return cached response if present (cached: True).
    4. Compute/query MongoDB, cache the result with a 300s TTL, and return (cached: False).
    """
    # 1. Extract company_id and user_id from JWT current_user
    company_id = current_user.get("company_id") if current_user else None
    user_id = str(current_user.get("id") or current_user.get("_id") or "") if current_user else ""

    # Fallback to TenantContext if needed
    if not company_id:
        try:
            ctx = await get_tenant_context(request)
            company_id = ctx.company_id
            user_id = ctx.user_id
        except Exception:
            pass

    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: company_id is required in user credentials.",
        )

    # 2. Normalize and hash the question
    q_hash = hash_question(payload.question)

    # 3. Check two-tier cache (L1 in-memory TTL + L2 Redis)
    cached_response = await cache_get(
        company_id=str(company_id),
        namespace="chat_answer",
        key=q_hash,
    )
    if cached_response is not None:
        if isinstance(cached_response, dict):
            out = dict(cached_response)
            out["cached"] = True
            return out
        return {"answer": cached_response, "cached": True}

    # 4. Cache miss: Compute answer from database
    result = await service.answer(
        company_id=str(company_id),
        user_id=user_id or "user",
        question=payload.question,
        history=payload.history,
        conversation_id=payload.conversation_id,
    )

    # 5. Cache result with 300 seconds TTL
    await cache_set(
        company_id=str(company_id),
        namespace="chat_answer",
        key=q_hash,
        value=result,
        ttl_seconds=300,
    )

    out = dict(result) if isinstance(result, dict) else {"result": result}
    out["cached"] = False
    return out
