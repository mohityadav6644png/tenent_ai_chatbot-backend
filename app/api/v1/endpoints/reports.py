"""
Reports Endpoints (app/api/v1/endpoints/reports.py)
Company admin management of scheduled reports and automated runner execution.
"""

from fastapi import APIRouter, Depends
from app.api.deps import require_roles, require_service_token, verify_csrf
from app.api.v1.schemas.reports import (
    ReportRunRequest,
    ReportRunResponse,
    ReportsListResponse,
    ReportStatusResponse,
)
from app.container import get_report_service
from app.core.context import TenantContext
from app.modules.reports.service import ReportService

router = APIRouter(tags=["Reports"])


@router.get("/reports", response_model=ReportsListResponse)
async def list_reports(
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: ReportService = Depends(get_report_service),
):
    items = await service.list_reports(company_id=ctx.company_id)
    return {"reports": items}


@router.post("/reports/{id}/pause", response_model=ReportStatusResponse, dependencies=[Depends(verify_csrf)])
async def pause_report(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: ReportService = Depends(get_report_service),
):
    return await service.pause_report(company_id=ctx.company_id, report_id=id)


@router.post("/reports/{id}/resume", response_model=ReportStatusResponse, dependencies=[Depends(verify_csrf)])
async def resume_report(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: ReportService = Depends(get_report_service),
):
    return await service.resume_report(company_id=ctx.company_id, report_id=id)


@router.post("/internal/reports/run", response_model=ReportRunResponse)
async def run_report_internal(
    data: ReportRunRequest,
    authorized: bool = Depends(require_service_token),
    service: ReportService = Depends(get_report_service),
):
    """Internal report runner triggered by automation tools (e.g. n8n) via service token."""
    return await service.run_report(report_id=data.report_id)
