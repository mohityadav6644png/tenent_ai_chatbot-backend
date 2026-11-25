"""
Connection Schemas (app/api/v1/schemas/connections.py)
Request and response models for admin-defined file connections.
"""

from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class ConnectionPartySchema(BaseModel):
    file_id: str
    file_name: str
    key: str


class ConnectionListItemSchema(BaseModel):
    id: str
    name: str
    left: ConnectionPartySchema
    right: ConnectionPartySchema
    alias: str
    status: str
    match_rate: float
    updated_at: Optional[datetime] = None


class ConnectionsListResponse(BaseModel):
    connections: List[ConnectionListItemSchema]


class PreviewConnectionRequest(BaseModel):
    left_file_id: str
    left_key: str
    right_file_id: str
    right_key: str


class PreviewConnectionResponse(BaseModel):
    ok: bool
    match_rate: float
    matched: int
    left_rows: int
    right_rows: int
    right_unique: bool
    duplicate_samples: List[Any] = Field(default_factory=list)
    unmatched_samples: List[Any] = Field(default_factory=list)
    warning: bool = False


class CreateConnectionRequest(BaseModel):
    left_file_id: str
    left_key: str
    right_file_id: str
    right_key: str
    name: Optional[str] = None


class CreateConnectionResponse(BaseModel):
    id: str
    status: str


class ConnectionStatusResponse(BaseModel):
    id: str
    status: str
