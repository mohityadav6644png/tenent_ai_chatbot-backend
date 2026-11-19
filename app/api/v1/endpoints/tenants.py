"""
Tenants Endpoints (app/api/v1/endpoints/tenants.py)
Super admin management of tenant lifecycle, onboarding, status, and schema versions.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from app.api.deps import (
    bearer_scheme,
    get_current_user,
    get_db,
    require_roles,
    require_service_token,
    verify_csrf,
)
from app.api.v1.schemas.tenants import (
    SchemaSyncResponse,
    TenantCreateRequest,
    TenantCreateResponse,
    TenantDetailResponse,
    TenantListResponse,
    TenantOnboardRequest,
    TenantOnboardResponse,
    TenantStatusResponse,
)
from app.container import get_tenant_service
from app.core.config import settings
from app.core.context import TenantContext
from app.modules.tenants.service import TenantService, onboard_tenant_service

router = APIRouter(prefix="/internal/tenants", tags=["Tenants"])


@router.get("", response_model=TenantListResponse)
async def list_tenants(
    ctx: TenantContext = Depends(require_roles("super_admin")),
    service: TenantService = Depends(get_tenant_service),
):
    return await service.list_tenants()


@router.post("", response_model=TenantCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_csrf)])
async def create_tenant(
    data: TenantCreateRequest,
    ctx: TenantContext = Depends(require_roles("super_admin")),
    service: TenantService = Depends(get_tenant_service),
):
    return await service.create_tenant(
        company_name=data.company_name,
        admin_name=data.admin_name,
        admin_email=data.admin_email,
        monthly_query_limit=data.monthly_query_limit,
        actor=ctx.name or ctx.user_id or "super_admin",
    )


@router.get("/{id}", response_model=TenantDetailResponse)
async def get_tenant_detail(
    id: str,
    ctx: TenantContext = Depends(require_roles("super_admin")),
    service: TenantService = Depends(get_tenant_service),
):
    return await service.get_tenant_detail(id)


@router.post("/{id}/suspend", response_model=TenantStatusResponse, dependencies=[Depends(verify_csrf)])
async def suspend_tenant(
    id: str,
    ctx: TenantContext = Depends(require_roles("super_admin")),
    service: TenantService = Depends(get_tenant_service),
):
    return await service.suspend_tenant(id, actor=ctx.name or ctx.user_id or "super_admin")


@router.post("/{id}/reactivate", response_model=TenantStatusResponse, dependencies=[Depends(verify_csrf)])
async def reactivate_tenant(
    id: str,
    ctx: TenantContext = Depends(require_roles("super_admin")),
    service: TenantService = Depends(get_tenant_service),
):
    return await service.reactivate_tenant(id, actor=ctx.name or ctx.user_id or "super_admin")


@router.post("/{id}/schema/sync", response_model=SchemaSyncResponse)
async def sync_tenant_schema(
    id: str,
    request: Request,
    service: TenantService = Depends(get_tenant_service),
):
    # Allowed via super_admin session OR valid service token header
    st_header = request.headers.get("X-Service-Token")
    session_cookie = request.cookies.get("session")
    if not st_header and not session_cookie:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return await service.sync_schema(id, actor="super_admin_or_service")


# Legacy route retention
legacy_tenants_router = APIRouter(prefix="/tenants", tags=["Tenants"])


@legacy_tenants_router.post("/", response_model=TenantOnboardResponse, status_code=status.HTTP_201_CREATED)
async def onboard_company(request: Request, payload: TenantOnboardRequest, db=Depends(get_db), current_user=Depends(get_current_user)):
    if current_user.get("role") not in ["platform_admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Super Admin privileges required.")
    client = request.app.mongodb_client
    return await onboard_tenant_service(db, client, payload)
