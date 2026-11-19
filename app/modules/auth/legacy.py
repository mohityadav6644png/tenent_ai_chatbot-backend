"""
Legacy Auth Helpers (app/modules/auth/legacy.py)
Backward-compatibility helper functions for tests and legacy callers.
"""

from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId
from fastapi import HTTPException, status
from app.infrastructure.db.mongodb import users_collection, revoked_tokens_collection
from app.modules.auth.models import create_user_document
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_email_verification_token,
    decode_token,
)
from app.core.email import send_verification_email


def _serialize_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "username": user.get("username"),
        "email": user["email"],
        "name": user.get("name") or user.get("username"),
        "role": user.get("role", "employee"),
        "is_verified": user.get("is_verified", False),
    }


async def register_user(username: str, email: str, password: str, name: Optional[str] = None) -> dict:
    email_clean = email.strip().lower()
    existing = await users_collection.find_one({"email": email_clean})
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists")
    hashed = hash_password(password)
    user_doc = create_user_document(username=username, email=email_clean, hashed_password=hashed, name=name)
    await users_collection.insert_one(user_doc)
    verify_token = create_email_verification_token(email_clean)
    send_verification_email(email=email_clean, username=user_doc["name"], token=verify_token)
    return _serialize_user(user_doc)


async def verify_email(token: str) -> dict:
    payload = decode_token(token)
    email = payload.get("sub")
    user = await users_collection.find_one({"email": email.lower()})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await users_collection.update_one({"_id": user["_id"]}, {"$set": {"is_verified": True}})
    return {"message": "Email has been successfully verified!", "success": True}


async def resend_verification_email(email: str) -> dict:
    user = await users_collection.find_one({"email": email.strip().lower()})
    if not user:
        raise HTTPException(status_code=404, detail="No account found")
    token = create_email_verification_token(email)
    send_verification_email(email, user.get("name", "User"), token)
    return {"message": "Verification email sent successfully", "success": True}


async def login_user(email: str, password: str) -> dict:
    user = await users_collection.find_one({"email": email.strip().lower()})
    if not user or not verify_password(password, user.get("password", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    uid = str(user["_id"])
    return {
        "access_token": create_access_token(uid),
        "refresh_token": create_refresh_token(uid),
        "token_type": "bearer",
        "user": _serialize_user(user),
    }


async def refresh_user_token(refresh_token: str) -> dict:
    payload = decode_token(refresh_token)
    uid = payload.get("sub")
    user = await users_collection.find_one({"_id": ObjectId(uid)})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return {
        "access_token": create_access_token(uid),
        "refresh_token": create_refresh_token(uid),
        "token_type": "bearer",
        "user": _serialize_user(user),
    }


async def logout_user(token: str, user_id: Optional[str] = None) -> dict:
    if token:
        try:
            await revoked_tokens_collection.insert_one({"token": token, "user_id": user_id, "created_at": datetime.now(timezone.utc)})
        except Exception:
            pass
    return {"message": "Logged out successfully", "success": True}


async def authenticate_super_admin(email: str, password: str) -> dict:
    user = await users_collection.find_one({"email": email.strip().lower()})
    if not user or user.get("role") not in ["super_admin", "platform_admin"]:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(password, user.get("password") or user.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    uid = str(user["_id"])
    return {
        "access_token": create_access_token(uid),
        "refresh_token": create_refresh_token(uid),
        "token_type": "bearer",
        "user": _serialize_user(user),
    }
