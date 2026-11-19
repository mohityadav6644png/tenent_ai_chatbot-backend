"""
Auth Models (app/modules/auth/models.py)
Document structures for users, sessions, invites, and password resets in MongoDB.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from bson import ObjectId


def create_user_document(
    username: str,
    email: str,
    hashed_password: str,
    name: Optional[str] = None,
    role: str = "employee",
    company_id: Optional[str] = None,
    is_active: bool = True,
    is_invited: bool = False,
) -> dict:
    """User MongoDB document model"""
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "username": username.strip(),
        "email": email.strip().lower(),
        "name": (name or username).strip(),
        "password": hashed_password,
        "is_verified": False,
        "role": role,
        "company_id": company_id,
        "is_active": is_active,
        "is_invited": is_invited,
        "last_login_at": None,
        "created_at": now,
        "updated_at": now,
    }


def create_session_document(
    user_id: str,
    token_hash: str,
    ttl_minutes: int,
    ip: str = "127.0.0.1",
    user_agent: str = "unknown",
) -> dict:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=ttl_minutes)
    return {
        "_id": ObjectId(),
        "user_id": user_id,
        "token_hash": token_hash,
        "expires_at": expires_at,
        "ip": ip,
        "user_agent": user_agent,
        "created_at": now,
    }


def create_invite_document(
    company_id: str,
    email: str,
    name: str,
    role: str,
    token_hash: str,
    expires_days: int = 7,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "company_id": company_id,
        "email": email.strip().lower(),
        "name": name.strip(),
        "role": role,
        "token_hash": token_hash,
        "status": "pending",
        "expires_at": now + timedelta(days=expires_days),
        "created_at": now,
    }


def create_password_reset_document(
    user_id: str,
    email: str,
    token_hash: str,
    expires_hours: int = 1,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "user_id": user_id,
        "email": email.strip().lower(),
        "token_hash": token_hash,
        "used": False,
        "expires_at": now + timedelta(hours=expires_hours),
        "created_at": now,
    }
