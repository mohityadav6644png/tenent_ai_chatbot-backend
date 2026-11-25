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
    status: Optional[str] = None,
    must_change_password: Optional[bool] = None,
) -> dict:
    """User MongoDB document model"""
    now = datetime.now(timezone.utc)
    user_status = status if status is not None else ("invited" if is_invited else ("active" if is_active else "inactive"))
    change_pwd = must_change_password if must_change_password is not None else (True if is_invited else False)
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
        "status": user_status,
        "must_change_password": change_pwd,
        "activated_at": None,
        "last_login_at": None,
        "created_at": now,
        "updated_at": now,
    }


def create_invite_token_document(
    token_hash: str,
    company_id: str,
    user_id: str,
    expires_at: datetime,
) -> dict:
    """Invite token document for one-time onboarding activation"""
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "token_hash": token_hash,
        "company_id": str(company_id),
        "user_id": str(user_id),
        "used": False,
        "expires_at": expires_at,
        "created_at": now,
        "used_at": None,
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
