"""
Health Schemas (app/api/v1/schemas/health.py)
Request and response models for public probes and super-admin internal health monitoring.
"""

from datetime import datetime
from typing import Dict, List, Literal
from pydantic import BaseModel


class HealthComponent(BaseModel):
    key: Literal["api", "database", "openai", "cache"]
    label: str
    status: Literal["operational", "degraded", "down"]
    checked_at: datetime


class InternalHealthResponse(BaseModel):
    components: List[HealthComponent]


class LivenessResponse(BaseModel):
    status: str = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    checks: Dict[str, str]
