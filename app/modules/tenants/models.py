"""
Tenants Models (app/modules/tenants/models.py)
Document model definitions for multi-tenant organizations.
"""

from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId


def create_tenant_document(
    name: str,
    monthly_query_limit: int,
    status: str = "onboarding",
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "name": name.strip(),
        "status": status,
        "monthly_query_limit": monthly_query_limit,
        "schema_version": 1,
        "created_at": now,
        "updated_at": now,
    }
