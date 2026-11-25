"""
Core Security (app/core/security.py)
Argon2 hashing, opaque session tokens, CSRF tokens, and constant-time comparisons.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Optional
import jwt
from pwdlib import PasswordHash
from app.core.config import settings

password_hash = PasswordHash.recommended()
DUMMY_ARGON2_HASH = password_hash.hash("dummy_constant_time_string")


def hash_password(password: str) -> str:
    """Hash password using Argon2id"""
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against Argon2id hash in constant-time"""
    try:
        return password_hash.verify(plain_password, hashed_password)
    except Exception:
        return False


def verify_dummy_password(plain_password: str) -> bool:
    """Execute constant-time Argon2 computation when user is not found to prevent timing leaks."""
    try:
        return password_hash.verify(plain_password, DUMMY_ARGON2_HASH)
    except Exception:
        return False


def generate_opaque_token(nbytes: int = 32) -> str:
    """Generate 256-bit opaque cryptographically secure random token."""
    return secrets.token_hex(nbytes)


def generate_invite_token(nbytes: int = 32) -> str:
    """Generate cryptographically secure URL-safe invite token."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Return SHA-256 hash of a token for secure database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    """Generate cryptographically secure CSRF token."""
    return secrets.token_urlsafe(32)


def constant_time_compare(val1: str, val2: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    return secrets.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))


def create_access_token(
    user_id: str,
    role: Optional[str] = None,
    company_id: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
    **extra_claims,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "type": "access", "exp": expire}
    if role is not None:
        payload["role"] = role
    if company_id is not None:
        payload["company_id"] = str(company_id)
    if email is not None:
        payload["email"] = email
    if name is not None:
        payload["name"] = name
    payload.update(extra_claims)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "type": "refresh", "exp": expire}, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_email_verification_token(email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.VERIFY_EMAIL_EXPIRE_HOURS)
    return jwt.encode({"sub": email.lower(), "type": "verify_email", "exp": expire}, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_password_reset_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.RESET_PASSWORD_EXPIRE_HOURS)
    return jwt.encode({"sub": str(user_id), "email": email.lower(), "type": "reset_password", "exp": expire}, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
