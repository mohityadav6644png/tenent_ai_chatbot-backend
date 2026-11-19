"""
Users Models (app/modules/users/models.py)
Document model definitions for tenant user accounts and pending invites.
"""

from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId


def create_tenant_user_document(
    company_id: str,
    name: str,
    email: str,
    role: str = "employee",
    hashed_password: str = "PENDING_SETUP",
    is_active: bool = False,
    is_invited: bool = True,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "company_id": company_id,
        "name": name.strip(),
        "username": email.strip().lower().split("@")[0],
        "email": email.strip().lower(),
        "role": role,
        "password": hashed_password,
        "is_active": is_active,
        "is_invited": is_invited,
        "created_at": now,
        "updated_at": now,
    }
