"""
Company Endpoints (app/api/v1/endpoints/company.py)
Multi-tenant data ingestion endpoints for company administrators.
"""

from datetime import datetime, timezone
import io
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
import pandas as pd

from app.api.deps import get_db, get_tenant_context, require_roles
from app.api.v1.schemas.company import (
    CompanyCSVAutoImportResponse,
    CompanyCSVImportResponse,
    CompanyDataInsertRequest,
    CompanyDataInsertResponse,
    DatasetsListResponse,
    ImportDatasetResponse,
    ImportPreviewResponse,
)
from app.container import get_company_service
from app.core.context import TenantContext
from app.core.logging import request_id_ctx
from app.modules.company.service import CompanyDataError, CompanyService

router = APIRouter(tags=["Company data"])


def _handle_company_data_error(request: Request, exc: CompanyDataError) -> JSONResponse:
    req_id = getattr(request.state, "request_id", None) or request_id_ctx.get() or "unknown"
    err_body: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.status_code in (429, 503),
    }
    if exc.retry_after is not None:
        err_body["retry_after"] = exc.retry_after
    if exc.details is not None:
        err_body["details"] = exc.details

    headers = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "request_id": req_id,
            "status": "error",
            "error": err_body,
            "detail": exc.message,
        },
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Feature A: Free-text records
# ---------------------------------------------------------------------------


@router.post(
    "/data/insert",
    response_model=CompanyDataInsertResponse,
    status_code=status.HTTP_200_OK,
    summary="Manual Text Data Insertion",
)
async def insert_manual_data(
    request: Request,
    payload: CompanyDataInsertRequest,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    db: Any = Depends(get_db),
    service: CompanyService = Depends(get_company_service),
):
    """
    Manual Text Data Insertion:
    - Accepts title and content in JSON body.
    - Validates company_admin role in the JWT token.
    - Stitches tenant company_id exclusively from TenantContext.
    - Stitches created_by from TenantContext user_id.
    - Inserts into MongoDB company_records collection.
    - Returns success message and inserted record ID.
    """
    company_id = str(ctx.company_id)
    user_id = str(ctx.user_id) if ctx.user_id else None

    try:
        record_id = await service.insert_manual_record(
            company_id=company_id,
            title=payload.title,
            content=payload.content,
            user_id=user_id,
            db=db,
        )
    except CompanyDataError as exc:
        return _handle_company_data_error(request, exc)

    return {
        "message": "Record inserted successfully",
        "id": record_id,
        "record_id": record_id,
        "company_id": company_id,
        "success": True,
    }


@router.post(
    "/import-csv-data",
    response_model=CompanyCSVImportResponse,
    status_code=status.HTTP_200_OK,
    summary="CSV File Import & Column Mapping",
)
async def import_csv_data(
    request: Request,
    file: UploadFile = File(..., description="Uploaded CSV file"),
    title_column: Optional[str] = Form(None, description="Optional title column name"),
    content_column: Optional[str] = Form(None, description="Optional content column name"),
    column_mapping: Optional[str] = Form(
        None, description="Optional custom column mapping JSON (e.g. {'MyCol': 'title'})"
    ),
    ctx: TenantContext = Depends(require_roles("company_admin")),
    db: Any = Depends(get_db),
    service: CompanyService = Depends(get_company_service),
):
    """
    CSV File Import & Column Mapping:
    - Accepts an uploaded CSV file.
    - Parses byte stream using standard CSV parser without pandas.
    - Maps headers (via explicit params, column_mapping, or heuristic aliases).
    - Stretches and stamps every row with the admin's verified company_id, created_by, and UTC timestamp.
    - Executes bulk insert into MongoDB company_records collection in batches of 1,000.
    - Returns total count of successfully imported records.
    """
    company_id = str(ctx.company_id)
    user_id = str(ctx.user_id) if ctx.user_id else None

    try:
        content_bytes = await file.read()
    except Exception as exc:
        return _handle_company_data_error(
            request,
            CompanyDataError(f"Failed to read uploaded file: {exc!s}", code="BAD_REQUEST", status_code=400),
        )

    try:
        inserted_count, skipped_count, import_id = await service.import_csv_data(
            company_id=company_id,
            content_bytes=content_bytes,
            filename=file.filename or "upload.csv",
            user_id=user_id,
            title_column=title_column,
            content_column=content_column,
            custom_mapping_raw=column_mapping,
            db=db,
        )
    except CompanyDataError as exc:
        return _handle_company_data_error(request, exc)

    return {
        "message": f"Successfully imported {inserted_count} records.",
        "count": inserted_count,
        "skipped": skipped_count,
        "import_id": import_id,
        "total_records_imported": inserted_count,
        "imported_count": inserted_count,
        "records_count": inserted_count,
        "success": True,
    }


@router.post(
    "/import-csv-auto",
    response_model=CompanyCSVAutoImportResponse,
    status_code=status.HTTP_200_OK,
    summary="CSV File Import with Smart Column Auto-Detection",
)
async def import_csv_auto(
    file: UploadFile = File(..., description="Uploaded CSV file"),
    title_column: Optional[str] = Form(None, description="Optional title column name or 'auto-detect'"),
    content_column: Optional[str] = Form(None, description="Optional content column name or 'auto-detect'"),
    ctx: TenantContext = Depends(require_roles("company_admin")),
    db: Any = Depends(get_db),
):
    """
    Import CSV with smart column auto-detection using pandas.
    - Verified company_admin role from JWT claims.
    - Isolates records by company_id extracted securely from JWT claims.
    - Parses CSV into pandas DataFrame.
    - Auto-detects title & content columns or validates user-supplied columns.
    - Bulk inserts records into MongoDB company_records collection.
    """
    # 1. Tenant Security
    company_id = str(ctx.company_id) if ctx.company_id else None
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: company_id missing from credentials.",
        )

    # 2. Read file stream into memory
    try:
        content_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file: {exc!s}",
        )

    if not content_bytes or len(content_bytes.strip()) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded CSV file is empty.",
        )

    # 3. Parse with pandas
    try:
        df = pd.read_csv(io.BytesIO(content_bytes))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse CSV file: {exc!s}",
        )

    # Get column names
    columns = [str(c).strip() for c in df.columns]
    if not columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file contains no column headers.",
        )

    # 4. Auto-detect logic
    def is_auto(val: Optional[str]) -> bool:
        if val is None:
            return True
        s = val.strip()
        return not s or s.lower() == "auto-detect"

    # Title auto-detection: scan for keywords like "title", "document title", "name", "subject", "heading"
    title_keywords = ["title", "document title", "name", "subject", "heading"]
    resolved_title: Optional[str] = None
    if is_auto(title_column):
        for kw in title_keywords:
            for col in columns:
                col_lower = col.lower()
                if col_lower == kw or kw in col_lower:
                    resolved_title = col
                    break
            if resolved_title:
                break
        if not resolved_title:
            resolved_title = columns[0]
    else:
        resolved_title = title_column.strip()

    # Content auto-detection: scan for keywords like "content", "body content", "description", "text", "body", "details"
    content_keywords = ["content", "body content", "description", "text", "body", "details"]
    resolved_content: Optional[str] = None
    if is_auto(content_column):
        for kw in content_keywords:
            for col in columns:
                if col == resolved_title:
                    continue
                col_lower = col.lower()
                if col_lower == kw or kw in col_lower:
                    resolved_content = col
                    break
            if resolved_content:
                break
        if not resolved_content:
            # Fallback to second column (or first if only one exists)
            remaining = [c for c in columns if c != resolved_title]
            if remaining:
                resolved_content = remaining[0]
            elif len(columns) > 1:
                resolved_content = columns[1]
            else:
                resolved_content = columns[0]
    else:
        resolved_content = content_column.strip()

    # Validate that resolved title and content columns actually exist in the CSV headers
    if resolved_title not in df.columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Resolved title column '{resolved_title}' does not exist in CSV headers: {columns}",
        )

    if resolved_content not in df.columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Resolved content column '{resolved_content}' does not exist in CSV headers: {columns}",
        )

    # 5. Database Insertion
    now = datetime.now(timezone.utc)
    records = []
    for _, row in df.iterrows():
        raw_title = row[resolved_title]
        raw_content = row[resolved_content]

        title_val = "" if pd.isna(raw_title) else str(raw_title).strip()
        content_val = "" if pd.isna(raw_content) else str(raw_content).strip()

        # Skip completely empty rows
        if not title_val and not content_val:
            continue

        doc = {
            "company_id": company_id,
            "title": title_val,
            "content": content_val,
            "created_at": now,
        }
        if ctx.user_id:
            doc["created_by"] = str(ctx.user_id)
        records.append(doc)

    inserted_count = 0
    if records:
        collection = db["company_records"] if db is not None and hasattr(db, "__getitem__") else None
        if collection is None:
            from app.infrastructure.db.mongodb import database
            collection = database["company_records"]

        try:
            result = await collection.insert_many(records, ordered=False)
        except TypeError:
            result = await collection.insert_many(records)
        inserted_count = len(result.inserted_ids)

    # 6. Response
    return {
        "message": f"Successfully imported {inserted_count} records.",
        "total_records": inserted_count,
        "total_records_imported": inserted_count,
        "title_column": resolved_title,
        "content_column": resolved_content,
        "mapped_columns": {
            "title_column": resolved_title,
            "content_column": resolved_content,
        },
        "success": True,
    }


# ---------------------------------------------------------------------------
# Feature B: Structured Datasets
# ---------------------------------------------------------------------------


@router.get(
    "/datasets",
    response_model=DatasetsListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Structured Datasets",
)
async def list_datasets(
    request: Request,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: CompanyService = Depends(get_company_service),
):
    """
    List Structured Datasets:
    - Returns catalog of available datasets with record count, status, last updated, and required columns.
    """
    company_id = str(ctx.company_id)
    try:
        datasets = await service.list_datasets(company_id=company_id)
    except CompanyDataError as exc:
        return _handle_company_data_error(request, exc)

    return {"datasets": datasets}


@router.get(
    "/datasets/{dataset}/template",
    status_code=status.HTTP_200_OK,
    summary="Download Dataset CSV Template",
)
async def get_dataset_template(
    dataset: str,
    request: Request,
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: CompanyService = Depends(get_company_service),
):
    """
    Download Dataset CSV Template:
    - Returns a CSV file containing only the header row for the given dataset.
    """
    try:
        csv_text = service.get_dataset_template(dataset_key=dataset)
    except CompanyDataError as exc:
        return _handle_company_data_error(request, exc)

    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{dataset}_template.csv"'},
    )


@router.post(
    "/datasets/{dataset}/imports/preview",
    response_model=ImportPreviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview Dataset Import",
)
async def preview_dataset_import(
    dataset: str,
    request: Request,
    file: UploadFile = File(..., description="Uploaded CSV file to preview"),
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: CompanyService = Depends(get_company_service),
):
    """
    Preview Dataset Import:
    - Validates file without writing anything to the database.
    - Returns total_rows, valid_rows, invalid_rows, missing_columns, unknown_columns, sample_rows, and errors.
    """
    company_id = str(ctx.company_id)

    try:
        content_bytes = await file.read()
    except Exception as exc:
        return _handle_company_data_error(
            request,
            CompanyDataError(f"Failed to read uploaded file: {exc!s}", code="BAD_REQUEST", status_code=400),
        )

    try:
        preview_result = await service.preview_dataset_import(
            company_id=company_id,
            dataset_key=dataset,
            content_bytes=content_bytes,
            filename=file.filename or "upload.csv",
        )
    except CompanyDataError as exc:
        return _handle_company_data_error(request, exc)

    return preview_result


@router.post(
    "/datasets/{dataset}/imports",
    response_model=ImportDatasetResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Dataset Import",
)
async def execute_dataset_import(
    dataset: str,
    request: Request,
    file: UploadFile = File(..., description="Uploaded CSV file"),
    mode: str = Form("upsert", description="Import mode: 'upsert' or 'replace'"),
    ctx: TenantContext = Depends(require_roles("company_admin")),
    service: CompanyService = Depends(get_company_service),
):
    """
    Execute Dataset Import:
    - Ingests structured business records via 'upsert' or 'replace'.
    - Enforces concurrency guard, rate limiting, and cache invalidation.
    """
    company_id = str(ctx.company_id)
    user_id = str(ctx.user_id) if ctx.user_id else "unknown_admin"

    try:
        content_bytes = await file.read()
    except Exception as exc:
        return _handle_company_data_error(
            request,
            CompanyDataError(f"Failed to read uploaded file: {exc!s}", code="BAD_REQUEST", status_code=400),
        )

    try:
        import_result = await service.import_dataset(
            company_id=company_id,
            user_id=user_id,
            dataset_key=dataset,
            content_bytes=content_bytes,
            filename=file.filename or "upload.csv",
            mode=mode,
        )
    except CompanyDataError as exc:
        return _handle_company_data_error(request, exc)

    return import_result
