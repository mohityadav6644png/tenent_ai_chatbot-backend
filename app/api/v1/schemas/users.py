"""
Users Schemas (app/api/v1/schemas/users.py)
Request and response schemas for company admin user and invite operations.
"""

from typing import List, Literal
from pydantic import BaseModel, EmailStr, Field


class UsersSummary(BaseModel):
    total_users: int
    admins: int
    pending_invites: int


class UserListItem(BaseModel):
    id: str
    name: str
    email: str
    role: Literal["admin", "employee"]
    status: Literal["active", "invite_pending"]
    is_self: bool


class UsersListResponse(BaseModel):
    summary: UsersSummary
    users: List[UserListItem]


class InviteCreateRequest(BaseModel):
    name: str = Field(..., min_length=2)
    email: EmailStr
    role: Literal["employee", "admin"]


class InviteCreateResponse(BaseModel):
    id: str
    status: Literal["invite_pending"] = "invite_pending"


class UserDeactivateResponse(BaseModel):
    id: str
    status: Literal["inactive"] = "inactive"


class GenericIdResponse(BaseModel):
    id: str
