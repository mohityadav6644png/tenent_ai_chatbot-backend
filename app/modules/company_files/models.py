"""
Company Files Domain Models (app/modules/company_files/models.py)
Data structures and document factories for company files and rows.
Pure Python - no motor, pymongo, fastapi, or csv imports.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional


def slugify_name(name: str) -> str:
    """Generate safe, unique file slug from file name (lowercase, max 40 chars, start with letter)."""
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    clean = re.sub(r"_+", "_", clean)
    if not clean or not clean[0].isalpha():
        clean = f"file_{clean}".strip("_")
    slug = clean[:40].rstrip("_")
    return slug or "file_data"


def create_file_document(
    company_id: str,
    name: str,
    slug: str,
    description: str,
    original_filename: str,
    upload_mode: str,
    columns: List[Dict[str, Any]],
    import_id: str,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "company_id": str(company_id),
        "name": name.strip(),
        "slug": slug.strip().lower(),
        "description": description.strip(),
        "original_filename": original_filename,
        "status": "processing",
        "upload_mode": upload_mode,
        "row_count": 0,
        "columns": columns,
        "stats": {},
        "last_import_id": str(import_id),
        "created_at": now,
        "updated_at": now,
    }


def create_row_document(
    company_id: str,
    file_id: str,
    import_id: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "company_id": str(company_id),
        "file_id": str(file_id),
        "import_id": str(import_id),
        "data": data,
        "updated_at": now,
    }
