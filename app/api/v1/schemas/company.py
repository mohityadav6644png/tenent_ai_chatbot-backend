"""
Company Schemas (app/api/v1/schemas/company.py)
Request and response schemas for company data management and CSV imports.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyDataInsertRequest(BaseModel):
    """Payload for manual text record creation. company_id is intentionally omitted
    to prevent frontend spoofing and must be derived from the admin's JWT token."""
    title: str = Field(..., min_length=1, max_length=200, description="Title of the document or record")
    content: str = Field(..., min_length=1, max_length=20000, description="Content of the document or record")

    model_config = ConfigDict(extra="ignore")

    @field_validator("title", "content")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field must not be empty or blank")
        return stripped


class CompanyDataInsertResponse(BaseModel):
    message: str = "Record inserted successfully"
    id: str
    record_id: str | None = None
    company_id: str | None = None
    success: bool = True

    model_config = ConfigDict(from_attributes=True)


class CompanyCSVImportResponse(BaseModel):
    message: str = "CSV data imported successfully"
    count: int | None = None
    skipped: int = 0
    import_id: str | None = None
    total_records_imported: int
    imported_count: int | None = None
    records_count: int | None = None
    success: bool = True

    model_config = ConfigDict(from_attributes=True)


class CompanyCSVAutoImportResponse(BaseModel):
    message: str = "CSV data imported successfully"
    total_records: int
    total_records_imported: Optional[int] = None
    title_column: str
    content_column: str
    mapped_columns: Optional[Dict[str, str]] = None
    success: bool = True

    model_config = ConfigDict(extra="ignore")


class DatasetItemResponse(BaseModel):
    key: str
    label: str
    record_label: str = "record"
    records: int = 0
    last_updated: Optional[str] = None
    status: str = "ready"
    required_columns: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class DatasetsListResponse(BaseModel):
    datasets: List[DatasetItemResponse]

    model_config = ConfigDict(extra="ignore")


class ImportPreviewError(BaseModel):
    row: Optional[int] = None
    column: Optional[str] = None
    reason: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class ImportPreviewResponse(BaseModel):
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    required_columns: List[str] = Field(default_factory=list)
    missing_columns: List[str] = Field(default_factory=list)
    unknown_columns: List[str] = Field(default_factory=list)
    sample_rows: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[ImportPreviewError] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class ImportDatasetResponse(BaseModel):
    import_id: Optional[str] = None
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    status: str = "completed"

    model_config = ConfigDict(extra="ignore")
