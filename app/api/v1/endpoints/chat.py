"""
Chat Endpoints (app/api/v1/endpoints/chat.py)
NLP-to-SQL query execution, conversations list, and message feedback.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from app.api.deps import require_roles, verify_csrf
from app.api.v1.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    ConversationsListResponse,
)
from app.container import get_query_service
from app.core.context import TenantContext
from app.core.logging import request_id_ctx
from app.modules.chat.service import QueryService

router = APIRouter(tags=["Chat"])


@router.post("/chat/query", response_model=ChatQueryResponse, dependencies=[Depends(verify_csrf)])
async def execute_chat_query(
    data: ChatQueryRequest,
    request: Request,
    ctx: TenantContext = Depends(require_roles("employee", "company_admin")),
    service: QueryService = Depends(get_query_service),
):
    """Execute natural language SQL query scoped strictly by authenticated company_id."""
    req_id = getattr(request.state, "request_id", None) or request_id_ctx.get() or "unknown"
    history_dicts = [h.model_dump() for h in data.history]
    result = await service.answer(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        question=data.question,
        history=history_dicts,
        conversation_id=data.conversation_id,
    )
    result["request_id"] = req_id
    return result


@router.get("/conversations", response_model=ConversationsListResponse)
async def list_conversations(
    ctx: TenantContext = Depends(require_roles("employee", "company_admin")),
    service: QueryService = Depends(get_query_service),
):
    items = await service.list_conversations(user_id=ctx.user_id, company_id=ctx.company_id)
    return {"conversations": items}
