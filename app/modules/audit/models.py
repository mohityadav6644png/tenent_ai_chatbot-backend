"""
Audit Models (app/modules/audit/models.py)
Document model for audit log entries storing metadata only.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from bson import ObjectId


def create_audit_event_document(
    actor: str,
    action: str,
    target: str,
    result: str = "success",
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    return {
        "_id": ObjectId(),
        "occurred_at": datetime.now(timezone.utc),
        "actor": actor,
        "action": action,
        "target": target,
        "result": result,
        "metadata": metadata or {},
    }
