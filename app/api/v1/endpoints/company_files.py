"""
Company Files Endpoints (app/api/v1/endpoints/company_files.py)
Multi-tenant CSV file management for company administrators.
"""

import json
from typing import Any, Dict

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Response,
    UploadFile,
    status,
)

from app.api.deps import require_roles, verify_csrf
from app.api.v1.schemas.company_files import (
    CompanyFileDetailResponse,
    CompanyFilePreviewResponse,
    CompanyFilesListResponse,
    CreateCompanyFileMeta,
    CreateCompanyFileResponse,
    PatchCompanyFileRequest,
    UploadCompanyFileResponse,
)
from app.container import get_company_files_service
from app.core.context import TenantContext
from app.core.exceptions import ValidationError
from app.modules.company_files.service import CompanyFilesService

router = APIRouter(tags=["Company files"])


@router.get("", response_model=CompanyFilesListResponse)
async def list_company_files(
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: CompanyFilesService = Depends(get_company_files_service),
) -> Dict[str, Any]:
    """List all company files and aggregate summary."""
    return await svc.list_files(company_id=str(ctx.company_id))


@router.post(
    "/preview",
    response_model=CompanyFilePreviewResponse,
    dependencies=[Depends(verify_csrf)],
)
async def preview_company_file(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: CompanyFilesService = Depends(get_company_files_service),
) -> Dict[str, Any]:
    """Preview a CSV file without persisting data."""
    content_bytes = await file.read()
    return await svc.preview(
        company_id=str(ctx.company_id),
        content_bytes=content_bytes,
        filename=file.filename or "file.csv",
    )


@router.post(
    "",
    response_model=CreateCompanyFileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_company_file(
    file: UploadFile = File(...),
    meta: str = Form(...),
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: CompanyFilesService = Depends(get_company_files_service),
) -> Dict[str, Any]:
    """Create and ingest a new company file."""
    try:
        raw_meta = json.loads(meta)
    except Exception:
        raise ValidationError("Field 'meta' must be valid JSON.")

    validated_meta = CreateCompanyFileMeta.model_validate(raw_meta).model_dump()
    content_bytes = await file.read()

    return await svc.create_file(
        company_id=str(ctx.company_id),
        user_id=str(ctx.user_id),
        content_bytes=content_bytes,
        filename=file.filename or "file.csv",
        meta=validated_meta,
    )


@router.get("/{id}", response_model=CompanyFileDetailResponse)
async def get_company_file(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: CompanyFilesService = Depends(get_company_files_service),
) -> Dict[str, Any]:
    """Get detail and column configuration of a company file."""
    return await svc.get_file_detail(company_id=str(ctx.company_id), file_id=id)


@router.patch(
    "/{id}",
    response_model=CompanyFileDetailResponse,
    dependencies=[Depends(verify_csrf)],
)
async def patch_company_file(
    id: str,
    req: PatchCompanyFileRequest,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: CompanyFilesService = Depends(get_company_files_service),
) -> Dict[str, Any]:
    """Update file description, column meanings, or hidden flags. Modifies no rows."""
    updates = req.model_dump(exclude_unset=True)
    return await svc.patch_file(
        company_id=str(ctx.company_id),
        user_id=str(ctx.user_id),
        file_id=id,
        updates=updates,
    )


@router.post(
    "/{id}/upload",
    response_model=UploadCompanyFileResponse,
    dependencies=[Depends(verify_csrf)],
)
async def upload_company_file_rows(
    id: str,
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: CompanyFilesService = Depends(get_company_files_service),
) -> Dict[str, Any]:
    """Upload new CSV data for an existing company file (replace or append mode)."""
    content_bytes = await file.read()
    return await svc.upload_file(
        company_id=str(ctx.company_id),
        user_id=str(ctx.user_id),
        file_id=id,
        content_bytes=content_bytes,
        filename=file.filename or "file.csv",
    )


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_company_file(
    id: str,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    svc: CompanyFilesService = Depends(get_company_files_service),
) -> Response:
    """Delete a company file and all its associated rows."""
    await svc.delete_file(
        company_id=str(ctx.company_id),
        user_id=str(ctx.user_id),
        file_id=id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
