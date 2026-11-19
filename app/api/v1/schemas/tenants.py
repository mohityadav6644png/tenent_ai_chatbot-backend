"""
Tenants Schemas (app/api/v1/schemas/tenants.py)
Request and response models for tenant onboarding, administration, and schema sync.
"""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, EmailStr, Field


class TenantSummary(BaseModel):
    active_tenants: int
    api_uptime_pct: float
    avg_response_ms: float
    ai_tokens_month: int


class TenantListItem(BaseModel):
    id: str
    name: str
    status: Literal["active", "onboarding", "suspended"]
    user_count: int
    queries_month: int


class TenantListResponse(BaseModel):
    summary: TenantSummary
    tenants: List[TenantListItem]


class TenantCreateRequest(BaseModel):
    company_name: str = Field(..., min_length=2)
    admin_name: str = Field(..., min_length=2)
    admin_email: EmailStr
    monthly_query_limit: Literal[5000, 10000, 25000]


class TenantCreateResponse(BaseModel):
    id: str
    status: Literal["onboarding"] = "onboarding"
    invite_email_sent: bool = False


class TenantDetailStats(BaseModel):
    user_count: int
    queries_month: int
    ai_tokens_month: int
    avg_response_ms: float


class TenantAdminItem(BaseModel):
    id: str
    name: str
    email: str
    last_login_at: Optional[datetime] = None


class TenantDetailResponse(BaseModel):
    id: str
    name: str
    status: str
    stats: TenantDetailStats
    admins: List[TenantAdminItem]


class TenantStatusResponse(BaseModel):
    id: str
    status: str


class SchemaSyncResponse(BaseModel):
    id: str
    schema_version: int


# Backward compatibility models
class TenantOnboardRequest(BaseModel):
    company_name: str
    admin_name: str
    admin_email: EmailStr
    query_limit_per_month: int


class TenantResponseData(BaseModel):
    company_id: str
    company_name: str
    admin_email: str
    query_limit_per_month: int
    created_at: datetime


class TenantOnboardResponse(BaseModel):
    success: bool
    message: str
    data: TenantResponseData
