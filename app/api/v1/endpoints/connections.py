"""
Connection Endpoints (app/api/v1/endpoints/connections.py)
Manage admin-defined connections between company files.
"""

from typing import Any, Dict
from fastapi import APIRouter, Depends, Response, status

from app.api.deps import require_roles, verify_csrf
from app.api.v1.schemas.connections import (
    ConnectionScanResponse,
    ConnectionStatusResponse,
    ConnectionsListResponse,
    CreateConnectionRequest,
    CreateConnectionResponse,
    PreviewConnectionRequest,
    PreviewConnectionResponse,
)
from app.container import get_connection_detection_worker, get_file_connections_service
from app.core.context import TenantContext
from app.modules.file_connections.service import FileConnectionsService

router = APIRouter(tags=["Connections"])


@router.get("", response_model=ConnectionsListResponse)
async def list_connections(
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Dict[str, Any]:
    return await svc.list_connections(company_id=str(ctx.company_id))


@router.post(
    "/preview",
    response_model=PreviewConnectionResponse,
    dependencies=[Depends(verify_csrf)],
)
async def preview_connection(
    data: PreviewConnectionRequest,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Dict[str, Any]:
    return await svc.preview(
        company_id=str(ctx.company_id),
        left_file_id=data.left_file_id,
        left_key=data.left_key,
        right_file_id=data.right_file_id,
        right_key=data.right_key,
    )


@router.post(
    "",
    response_model=CreateConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_connection(
    data: CreateConnectionRequest,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Dict[str, Any]:
    return await svc.create_connection(
        company_id=str(ctx.company_id),
        user_id=str(ctx.user_id),
        left_file_id=data.left_file_id,
        left_key=data.left_key,
        right_file_id=data.right_file_id,
        right_key=data.right_key,
        name=data.name,
    )


@router.post(
    "/{id}/accept",
    response_model=ConnectionStatusResponse,
    dependencies=[Depends(verify_csrf)],
)
async def accept_suggestion(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Dict[str, Any]:
    return await svc.accept_suggestion(company_id=str(ctx.company_id), user_id=str(ctx.user_id), conn_id=id)


@router.post(
    "/{id}/dismiss",
    response_model=ConnectionStatusResponse,
    dependencies=[Depends(verify_csrf)],
)
async def dismiss_suggestion(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Dict[str, Any]:
    return await svc.dismiss_suggestion(company_id=str(ctx.company_id), user_id=str(ctx.user_id), conn_id=id)


@router.post(
    "/{id}/refresh",
    response_model=ConnectionStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_csrf)],
)
async def refresh_connection(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Dict[str, Any]:
    return await svc.refresh_connection(company_id=str(ctx.company_id), user_id=str(ctx.user_id), conn_id=id)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_connection(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Response:
    await svc.delete_connection(company_id=str(ctx.company_id), user_id=str(ctx.user_id), conn_id=id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/scan",
    response_model=ConnectionScanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_csrf)],
)
async def trigger_connection_scan(
    ctx: TenantContext = Depends(require_roles("company_admin")),
    worker: Any = Depends(get_connection_detection_worker),
) -> Dict[str, Any]:
    from app.infrastructure.azure.redis_cache import get_cache_client
    from app.core.exceptions import RateLimitedError
    cache = get_cache_client()
    key = f"rate:connections_scan:{ctx.company_id}"
    count = await cache.incr(key)
    if count == 1:
        await cache.expire(key, 3600)
    if count > 6:
        raise RateLimitedError("Manual scan rate limit of 6 per hour exceeded.", retry_after=3600)

    import asyncio
    asyncio.create_task(worker.scan_company_connections(str(ctx.company_id), trigger="manual"))
    return {"status": "scanning", "message": "Connection scan initiated in background."}


@router.get(
    "/scan",
    response_model=ConnectionScanResponse,
)
async def get_connection_scan_status(
    ctx: TenantContext = Depends(require_roles("company_admin")),
    worker: Any = Depends(get_connection_detection_worker),
) -> Dict[str, Any]:
    return await worker.get_scan_state(str(ctx.company_id))


@router.post(
    "/{id}/reviewed",
    response_model=ConnectionStatusResponse,
    dependencies=[Depends(verify_csrf)],
)
async def mark_connection_reviewed(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: FileConnectionsService = Depends(get_file_connections_service),
) -> Dict[str, Any]:
    return await svc.mark_reviewed(company_id=str(ctx.company_id), user_id=str(ctx.user_id), conn_id=id)
