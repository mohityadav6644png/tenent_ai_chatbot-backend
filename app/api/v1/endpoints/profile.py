"""
Profile Endpoints (app/api/v1/endpoints/profile.py)
Endpoints for progressive user profile retrieval, manual editing, skipping, and deletion.
"""

from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Response, status
from app.api.deps import require_roles, verify_csrf
from app.api.v1.schemas.profile import (
    ProfilePatchRequest,
    ProfileSkipRequest,
    UserProfileResponse,
)
from app.container import get_audit_service, get_profile_repo, get_profile_service
from app.core.config import settings
from app.core.context import TenantContext
from app.infrastructure.db.profile_repository import MongoProfileRepository
from app.modules.audit.service import AuditService
from app.modules.profile.sanitizer import (
    sanitize_company_name,
    sanitize_interests,
    sanitize_name,
)
from app.modules.profile.service import ProfileService

router = APIRouter(tags=["Profile"])


@router.get("/profile", response_model=UserProfileResponse)
async def get_caller_profile(
    ctx: TenantContext = Depends(require_roles("employee", "company_admin", "super_admin")),
    service: ProfileService = Depends(get_profile_service),
):
    """Retrieve the authenticated caller's own profile."""
    prof = await service.get_or_init_profile(company_id=ctx.company_id, user_id=ctx.user_id)
    return prof


@router.patch("/profile", response_model=UserProfileResponse, dependencies=[Depends(verify_csrf)])
async def patch_caller_profile(
    data: ProfilePatchRequest,
    ctx: TenantContext = Depends(require_roles("employee", "company_admin", "super_admin")),
    service: ProfileService = Depends(get_profile_service),
    repo: MongoProfileRepository = Depends(get_profile_repo),
    audit: AuditService = Depends(get_audit_service),
):
    """Manually update the authenticated caller's own profile fields."""
    if settings.PROFILE_PREFILL_FROM_ACCOUNT:
        if data.name is not None or data.company_name is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account fields cannot be edited when prefill is enabled",
            )

    validated: Dict[str, Any] = {}
    if data.name is not None and (clean_name := sanitize_name(data.name)):
        validated["name"] = clean_name
    if data.company_name is not None and (clean_comp := sanitize_company_name(data.company_name)):
        validated["company_name"] = clean_comp
    if data.interest is not None and (clean_int := sanitize_interests(data.interest)):
        validated["interest"] = clean_int

    updated = await repo.merge_profile_fields(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        validated_fields=validated,
        source="user_edit",
        corrections=list(validated.keys()),
    )
    await audit.log_event(
        actor=ctx.user_id,
        action="profile_updated",
        target="profile",
        result="success",
        metadata={"fields": list(validated.keys()), "action": "update"},
    )
    return updated


@router.post("/profile/skip", dependencies=[Depends(verify_csrf)])
async def skip_caller_profile_field(
    data: ProfileSkipRequest,
    ctx: TenantContext = Depends(require_roles("employee", "company_admin", "super_admin")),
    repo: MongoProfileRepository = Depends(get_profile_repo),
    audit: AuditService = Depends(get_audit_service),
):
    """Skip a pending field or opt out of all future progressive profiling questions."""
    if data.field == "all":
        await repo.set_opted_out(company_id=ctx.company_id, user_id=ctx.user_id)
    else:
        await repo.set_field_status(company_id=ctx.company_id, user_id=ctx.user_id, field_name=data.field, status="skipped")

    await audit.log_event(
        actor=ctx.user_id,
        action="profile_skipped",
        target="profile",
        result="success",
        metadata={"field": data.field, "action": "skip"},
    )
    return {"status": "ok"}


@router.delete("/profile", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_csrf)])
async def delete_caller_profile(
    ctx: TenantContext = Depends(require_roles("employee", "company_admin", "super_admin")),
    repo: MongoProfileRepository = Depends(get_profile_repo),
    audit: AuditService = Depends(get_audit_service),
):
    """Delete the authenticated caller's own profile and progressive session states."""
    await repo.delete_profile_and_states(company_id=ctx.company_id, user_id=ctx.user_id)
    await audit.log_event(
        actor=ctx.user_id,
        action="profile_deleted",
        target="profile",
        result="success",
        metadata={"action": "delete"},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
