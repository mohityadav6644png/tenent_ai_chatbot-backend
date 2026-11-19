import sys
from pathlib import Path
from bson import ObjectId
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_email_verification_token,
    create_password_reset_token,
    decode_token,
)
from app.modules.auth.models import create_user_document
from app.api.v1.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    VerifyEmailRequest,
    ResendVerificationRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    RefreshTokenRequest,
)


def test_password_hashing():
    raw = "MySecurePassword123!"
    hashed = hash_password(raw)
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_tokens_lifecycle():
    uid = str(ObjectId())
    acc = create_access_token(uid)
    ref = create_refresh_token(uid)

    assert len(acc) > 20
    assert len(ref) > 20

    # Decode and verify access token
    acc_payload = decode_token(acc)
    assert acc_payload["sub"] == uid
    assert acc_payload["type"] == "access"

    # Decode and verify refresh token
    ref_payload = decode_token(ref)
    assert ref_payload["sub"] == uid
    assert ref_payload["type"] == "refresh"


def test_email_verification_token():
    email = "user@company.com"
    token = create_email_verification_token(email)
    payload = decode_token(token)

    assert payload["sub"] == email
    assert payload["type"] == "verify_email"
    assert "exp" in payload


def test_password_reset_token():
    uid = str(ObjectId())
    email = "admin@company.com"
    token = create_password_reset_token(uid, email)
    payload = decode_token(token)

    assert payload["sub"] == uid
    assert payload["email"] == email
    assert payload["type"] == "reset_password"
    assert "exp" in payload


def test_user_document_creation():
    doc = create_user_document("alice", "alice@example.com", "fake_argon2_hash", name="Alice Wonderland")
    assert doc["username"] == "alice"
    assert doc["email"] == "alice@example.com"
    assert doc["name"] == "Alice Wonderland"
    assert doc["role"] == "employee"
    assert doc["is_verified"] is False
    assert "_id" in doc
    assert "created_at" in doc


def test_request_schemas_validation():
    reg = RegisterRequest(username="bob_user", email="bob@test.com", password="password123")
    assert reg.email == "bob@test.com"

    login = LoginRequest(email="bob@test.com", password="password123")
    assert login.email == "bob@test.com"

    verify = VerifyEmailRequest(token="test_token_123")
    assert verify.token == "test_token_123"

    resend = ResendVerificationRequest(email="bob@test.com")
    assert resend.email == "bob@test.com"

    forgot = ForgotPasswordRequest(email="bob@test.com")
    assert forgot.email == "bob@test.com"

    reset = ResetPasswordRequest(token="token_abc", new_password="new_strong_password")
    assert reset.new_password == "new_strong_password"

    ref = RefreshTokenRequest(refresh_token="ref_tok_xyz")
    assert ref.refresh_token == "ref_tok_xyz"
