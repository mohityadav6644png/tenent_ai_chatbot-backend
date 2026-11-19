"""
Users Endpoints (app/api/v1/endpoints/users.py)
Company admin user management and invite lifecycle, strictly scoped to company_id.
"""

from fastapi import APIRouter, Depends, Response, status
from app.api.deps import require_roles, verify_csrf
from app.api.v1.schemas.users import (
    GenericIdResponse,
    InviteCreateRequest,
    InviteCreateResponse,
    UserDeactivateResponse,
    UsersListResponse,
)
from app.container import get_auth_repo, get_user_service
from app.core.context import TenantContext
from app.modules.users.service import UserService

router = APIRouter(tags=["Users"])


@router.get("/users", response_model=UsersListResponse)
async def list_users(
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: UserService = Depends(get_user_service),
):
    return await service.list_users(company_id=ctx.company_id, current_user_id=ctx.user_id)


@router.post("/invites", response_model=InviteCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_csrf)])
async def create_invite(
    data: InviteCreateRequest,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: UserService = Depends(get_user_service),
):
    auth_repo = get_auth_repo()
    tenant = await auth_repo.get_tenant_by_id(ctx.company_id)
    cname = tenant.get("name", "Your Organization") if tenant else "Your Organization"
    return await service.create_invite(
        company_id=ctx.company_id,
        company_name=cname,
        name=data.name,
        email=data.email,
        role=data.role,
        inviter_name=ctx.name or "Company Administrator",
    )


@router.post("/invites/{id}/resend", response_model=GenericIdResponse, dependencies=[Depends(verify_csrf)])
async def resend_invite(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: UserService = Depends(get_user_service),
):
    auth_repo = get_auth_repo()
    tenant = await auth_repo.get_tenant_by_id(ctx.company_id)
    cname = tenant.get("name", "Your Organization") if tenant else "Your Organization"
    return await service.resend_invite(
        company_id=ctx.company_id,
        company_name=cname,
        invite_id=id,
        inviter_name=ctx.name or "Company Administrator",
    )


@router.delete("/invites/{id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_csrf)])
async def delete_invite(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: UserService = Depends(get_user_service),
):
    await service.delete_invite(company_id=ctx.company_id, invite_id=id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{id}/deactivate", response_model=UserDeactivateResponse, dependencies=[Depends(verify_csrf)])
async def deactivate_user(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: UserService = Depends(get_user_service),
):
    return await service.deactivate_user(
        company_id=ctx.company_id,
        target_user_id=id,
        current_user_id=ctx.user_id,
    )
