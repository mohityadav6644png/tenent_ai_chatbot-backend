"""
Company Files Schemas (app/api/v1/schemas/company_files.py)
Request and response Pydantic models for admin-defined company CSV files.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CompanyFileColumnMeta(BaseModel):
    key: str
    meaning: Optional[str] = Field(None, max_length=120)
    hidden: bool = False


class CreateCompanyFileMeta(BaseModel):
    name: str = Field(..., min_length=2, max_length=60)
    description: str = Field(..., min_length=5, max_length=500)
    upload_mode: str = Field("replace", pattern="^(replace|append)$")
    columns: Optional[List[CompanyFileColumnMeta]] = None


class PatchCompanyFileRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=60)
    description: Optional[str] = Field(None, min_length=5, max_length=500)
    upload_mode: Optional[str] = Field(None, pattern="^(replace|append)$")
    columns: Optional[List[CompanyFileColumnMeta]] = None


class PreviewColumnResponse(BaseModel):
    key: str
    label: str
    type: str  # text | number | date | boolean
    example: Any = None
    suggested_hidden: bool = False


class FileDetailColumnResponse(BaseModel):
    key: str
    label: str
    type: str
    example: Any = None
    meaning: Optional[str] = None
    hidden: bool = False


class PreviewError(BaseModel):
    row: int
    column: Optional[str] = None
    reason: str


class PreviewLimits(BaseModel):
    max_files: int
    max_mb: int
    max_rows: int
    max_columns: int


class CompanyFilePreviewResponse(BaseModel):
    filename: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    columns: List[PreviewColumnResponse]
    sample_rows: List[Dict[str, Any]]
    errors: List[PreviewError]
    limits: PreviewLimits


class CreateCompanyFileResponse(BaseModel):
    id: str
    status: str
    inserted: int
    skipped: int


class UploadCompanyFileResponse(BaseModel):
    inserted: int
    skipped: int
    errors: List[PreviewError]
    status: str


class CompanyFileListItem(BaseModel):
    id: str
    name: str
    description: str
    rows: int
    columns_count: int
    hidden_columns_count: int = 0
    updated_at: datetime
    status: str
    upload_mode: str


class CompanyFilesSummary(BaseModel):
    files: int
    total_rows: int
    last_upload: Optional[datetime] = None
    limit: int


class CompanyFilesListResponse(BaseModel):
    summary: CompanyFilesSummary
    files: List[CompanyFileListItem]


class CompanyFileDetailResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    status: str
    upload_mode: str
    row_count: int
    columns: List[FileDetailColumnResponse]
    created_at: datetime
    updated_at: datetime
