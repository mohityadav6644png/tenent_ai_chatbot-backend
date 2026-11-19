"""
Reports Schemas (app/api/v1/schemas/reports.py)
Request and response models for scheduled reports management and execution.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class ReportSchedule(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"]
    time: str = Field(..., pattern="^([01]?[0-9]|2[0-3]):[0-5][0-9]$", description="HH:mm format")
    weekday: Optional[int] = Field(None, ge=0, le=6)
    day_of_month: Optional[int] = Field(None, ge=1, le=28)


class ReportRecipient(BaseModel):
    id: str
    name: str


class ReportItem(BaseModel):
    id: str
    name: str
    schedule: ReportSchedule
    recipients: List[ReportRecipient]
    status: Literal["active", "paused"]


class ReportsListResponse(BaseModel):
    reports: List[ReportItem]


class ReportStatusResponse(BaseModel):
    id: str
    status: Literal["active", "paused"]


class ReportRunRequest(BaseModel):
    report_id: str


class ReportRunResponse(BaseModel):
    report_id: str
    status: str
    recipients_emailed: int
