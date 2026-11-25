"""
File Connections Domain Models (app/modules/file_connections/models.py)
Domain representations for admin-defined file connections.
Pure Python - strictly no motor, pymongo, fastapi, or csv imports.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Literal, Optional

SLUG_REGEX = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

ConnectionStatus = Literal["suggested", "refreshing", "active", "needs_attention", "dismissed"]
Cardinality = Literal["many_to_one", "one_to_one"]


def is_valid_slug(slug: str) -> bool:
    """A file slug must match ^[a-z][a-z0-9_]{1,40}$ (no dots or hyphens)."""
    return bool(SLUG_REGEX.match(slug or ""))


@dataclass
class FileConnection:
    id: str
    company_id: str
    name: str
    left_file_id: str
    left_key: str
    right_file_id: str
    right_key: str
    alias: str
    cardinality: Cardinality = "many_to_one"
    status: ConnectionStatus = "suggested"
    match_rate: float = 0.0
    matched_rows: int = 0
    left_rows: int = 0
    right_rows: int = 0
    created_by: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def create_connection_document(
    company_id: str,
    name: str,
    left_file_id: str,
    left_key: str,
    right_file_id: str,
    right_key: str,
    alias: str,
    cardinality: Cardinality = "many_to_one",
    status: ConnectionStatus = "suggested",
    match_rate: float = 0.0,
    matched_rows: int = 0,
    left_rows: int = 0,
    right_rows: int = 0,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "company_id": str(company_id),
        "name": name.strip(),
        "left_file_id": str(left_file_id),
        "left_key": str(left_key).strip(),
        "right_file_id": str(right_file_id),
        "right_key": str(right_key).strip(),
        "alias": str(alias).strip().lower(),
        "cardinality": cardinality,
        "status": status,
        "match_rate": round(float(match_rate), 4),
        "matched_rows": int(matched_rows),
        "left_rows": int(left_rows),
        "right_rows": int(right_rows),
        "created_by": str(created_by) if created_by else None,
        "created_at": now,
        "updated_at": now,
    }
