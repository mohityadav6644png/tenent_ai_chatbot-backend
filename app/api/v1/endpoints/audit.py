"""
Audit Endpoints (app/api/v1/endpoints/audit.py)
Super admin access to immutable metadata audit log entries.
"""

from fastapi import APIRouter, Depends, Query
from app.api.deps import require_roles
from app.api.v1.schemas.audit import AuditListResponse
from app.container import get_audit_service
from app.core.context import TenantContext
from app.modules.audit.service import AuditService

router = APIRouter(prefix="/internal/audit", tags=["Audit"])


@router.get("", response_model=AuditListResponse)
async def list_audit_events(
    limit: int = Query(50, ge=1, le=100),
    ctx: TenantContext = Depends(require_roles("super_admin")),
    service: AuditService = Depends(get_audit_service),
):
    """Super admin audit log viewer: strictly returns metadata only, never SQL or business data."""
    events = await service.list_events(limit=limit)
    return {"events": events}
