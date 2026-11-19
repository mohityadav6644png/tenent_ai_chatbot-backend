"""
Company Records Models (app/modules/company/models.py)
Document factories for tenant company_records and data_imports.
"""

from datetime import UTC, datetime
from typing import Any


def create_company_record_document(
    company_id: str,
    title: str,
    content: str,
    created_at: datetime | None = None,
    created_by: str | None = None,
    import_id: str | None = None,
) -> dict[str, Any]:
    """Create a new company_records document stamped with tenant company_id and UTC timestamp."""
    doc: dict[str, Any] = {
        "company_id": str(company_id),
        "title": title.strip(),
        "content": content.strip(),
        "created_at": created_at or datetime.now(UTC),
    }
    if created_by is not None:
        doc["created_by"] = str(created_by)
    if import_id is not None:
        doc["import_id"] = str(import_id)
    return doc


def create_data_import_document(
    import_id: str,
    company_id: str,
    dataset: str,
    mode: str,
    created_by: str | None = None,
    filename: str | None = None,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """Create a new tracking record for dataset imports."""
    return {
        "import_id": import_id,
        "company_id": str(company_id),
        "dataset": dataset,
        "mode": mode,
        "created_by": str(created_by) if created_by else None,
        "filename": filename,
        "status": "in_progress",
        "started_at": started_at or datetime.now(UTC),
        "completed_at": None,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "error_message": None,
    }
