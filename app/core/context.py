"""
Core Context (app/core/context.py)
TenantContext: Tracks authenticated user_id, company_id (None for super_admin), role, and name.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TenantContext:
    user_id: Optional[str] = None
    company_id: Optional[str] = None
    role: str = "employee"
    name: Optional[str] = None
