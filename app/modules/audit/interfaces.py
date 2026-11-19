"""
Audit Interfaces (app/modules/audit/interfaces.py)
Protocol for audit logging repository.
"""

from typing import Any, Dict, List, Optional, Protocol


class AuditRepoProtocol(Protocol):
    async def record_event(self, doc: Dict[str, Any]) -> None: ...
    async def list_events(self, limit: int = 50) -> List[Dict[str, Any]]: ...
