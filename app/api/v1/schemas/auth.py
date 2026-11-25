"""
Auth Schemas (app/api/v1/schemas/auth.py)
Request and response models for authentication, sessions, invites, and password resets.
"""

from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class UserProfileResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    company_name: Optional[str] = None
    is_verified: Optional[bool] = False


class LoginResponse(BaseModel):
    user: UserProfileResponse
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: Optional[str] = "bearer"


class MeResponse(BaseModel):
    user: UserProfileResponse
    id: Optional[str] = None
    username: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None


class AcceptInviteRequest(BaseModel):
    token: str = Field(..., min_length=1)
    password: str = Field(..., min_length=10, description="Password must be at least 10 characters long")


class AcceptInviteResponse(BaseModel):
    access_token: str
    token_type: Optional[str] = "bearer"
    refresh_token: Optional[str] = None
    user: Optional[UserProfileResponse] = None
    message: Optional[str] = "Account successfully activated."
    success: Optional[bool] = True


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1)
    password: Optional[str] = Field(None, min_length=10)
    new_password: Optional[str] = Field(None, min_length=10)

    @property
    def target_password(self) -> str:
        pwd = self.password or self.new_password
        if not pwd or len(pwd) < 10:
            raise ValueError("Password must be at least 10 characters")
        return pwd


# Backward-compatibility schemas
class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    name: Optional[str] = None


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: Optional[dict] = None


class UserResponse(BaseModel):
    id: str
    username: Optional[str] = None
    email: str
    name: Optional[str] = None
    role: str
    is_verified: Optional[bool] = False


class MessageResponse(BaseModel):
    message: str
    success: bool = True
