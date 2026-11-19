"""
Audit Schemas (app/api/v1/schemas/audit.py)
Request and response models for audit events log.
"""

from datetime import datetime
from typing import List, Literal
from pydantic import BaseModel


class AuditEventItem(BaseModel):
    id: str
    occurred_at: datetime
    actor: str
    action: str
    target: str
    result: Literal["success", "retry", "failed", "logged"]


class AuditListResponse(BaseModel):
    events: List[AuditEventItem]
