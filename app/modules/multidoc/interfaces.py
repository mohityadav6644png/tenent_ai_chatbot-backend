"""
Multi-document Interfaces (app/modules/multidoc/interfaces.py)
Protocol definitions for multi-document repositories, planners, and synthesizers.
Pure Python - no motor, redis, google-genai, or fastapi imports.
"""

from typing import Any, Dict, List, Optional, Protocol, Set
from app.modules.multidoc.models import DocumentSummary, MultiDocQueryPlan


class MultiDocRepositoryProtocol(Protocol):
    """Protocol for fetching tenant documents and row records."""

    async def list_documents(
        self, company_id: str, limit: int = 6
    ) -> List[DocumentSummary]:
        ...

    async def get_document_records(
        self,
        company_id: str,
        document_id: str,
        limit: int = 200,
        hidden_keys: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        ...

    async def get_document_by_id_or_slug(
        self, company_id: str, doc_id_or_slug: str
    ) -> Optional[Dict[str, Any]]:
        ...


class MultiDocPlannerProtocol(Protocol):
    """Protocol for planning multi-document queries with LLM or rule engine."""

    async def plan_query(
        self,
        question: str,
        summaries: List[DocumentSummary],
        candidate_keys: List[str],
        history: List[Dict[str, str]],
        company_id: str,
    ) -> MultiDocQueryPlan:
        ...
