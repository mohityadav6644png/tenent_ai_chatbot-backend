"""
Company Files Schemas (app/api/v1/schemas/company_files.py)
Request and response Pydantic models for admin-defined company CSV files.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


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
    label: Optional[str] = None
    type: str = "text"  # text | number | date | boolean
    example: Any = None
    suggested_hidden: bool = False
    role: Optional[str] = None
    role_confidence: Optional[float] = None
    profile: Optional[Dict[str, Any]] = None
    linked_to: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="before")
    @classmethod
    def populate_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("label"):
                data["label"] = data.get("key", "")
            if not data.get("type"):
                data["type"] = "text"
        return data


class FileDetailColumnResponse(BaseModel):
    key: str
    label: Optional[str] = None
    type: str = "text"
    example: Any = None
    meaning: Optional[str] = None
    hidden: bool = False
    role: Optional[str] = None
    role_confidence: Optional[float] = None
    profile: Optional[Dict[str, Any]] = None
    linked_to: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="before")
    @classmethod
    def populate_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("label"):
                data["label"] = data.get("key", "")
            if not data.get("type"):
                data["type"] = "text"
        return data


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
    data_health: Optional[Dict[str, Any]] = None
    detected_connections: Optional[List[Dict[str, Any]]] = None


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
    columns: Optional[List[FileDetailColumnResponse]] = None


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
