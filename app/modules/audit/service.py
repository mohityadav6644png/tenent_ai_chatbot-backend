"""
Audit Service (app/modules/audit/service.py)
Audit logger recording metadata-only events for all admin and critical platform operations.
"""

from typing import Any, Dict, List, Optional
from app.modules.audit.interfaces import AuditRepoProtocol
from app.modules.audit.models import create_audit_event_document


class AuditService:
    def __init__(self, repo: AuditRepoProtocol):
        self.repo = repo

    async def log_event(
        self,
        actor: str,
        action: str,
        target: str,
        result: str = "success",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record metadata-only audit event without sensitive details."""
        safe_meta = {}
        if metadata:
            for k, v in metadata.items():
                if k.lower() not in ["sql", "password", "token", "rows", "question", "email"]:
                    safe_meta[k] = v
        doc = create_audit_event_document(actor=actor, action=action, target=target, result=result, metadata=safe_meta)
        await self.repo.record_event(doc)

    async def list_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await self.repo.list_events(limit=limit)
