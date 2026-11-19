"""
Reports Models (app/modules/reports/models.py)
Document model definitions for scheduled reports in MongoDB.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId


def create_report_document(
    company_id: str,
    name: str,
    query: str,
    schedule: Dict[str, Any],
    recipients: List[Dict[str, str]],
    status: str = "active",
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "company_id": str(company_id),
        "name": name.strip(),
        "query": query.strip(),
        "schedule": schedule,
        "recipients": recipients,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
