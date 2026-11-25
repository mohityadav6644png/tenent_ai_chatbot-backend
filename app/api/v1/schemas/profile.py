"""
Profile API Schemas (app/api/v1/schemas/profile.py)
Request and response schemas for progressive user profiling endpoints.
"""

from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field
from app.modules.profile.models import FieldSource, FieldStatus, ProfileFieldName


class ProfileFieldResponse(BaseModel):
    value: Optional[Union[str, List[str]]] = None
    status: FieldStatus
    source: Optional[FieldSource] = None


class UserProfileResponse(BaseModel):
    company_id: str
    user_id: str
    opted_out: bool
    completed: bool
    fields: Dict[str, ProfileFieldResponse]


class ProfilePatchRequest(BaseModel):
    name: Optional[str] = None
    company_name: Optional[str] = None
    interest: Optional[List[str]] = None


class ProfileSkipRequest(BaseModel):
    field: Union[ProfileFieldName, Literal["all"]]
