"""
Fake Multi-document Planner (app/infrastructure/multidoc_planner/fake_planner.py)
Deterministic rule-based query planner for tests and offline development.
"""

from typing import Any, Dict, List, Optional
from app.modules.multidoc.interfaces import MultiDocPlannerProtocol
from app.modules.multidoc.models import DocumentSummary, MultiDocQueryPlan

MULTIDOC_TRIGGER_KEYWORDS = [
    "both", "across", "compare", "combine", "together", "join",
    "all documents", "all files", "multiple files", "with",
]


class FakeMultiDocPlanner(MultiDocPlannerProtocol):
    def __init__(self, default_plan: Optional[MultiDocQueryPlan] = None):
        self.default_plan = default_plan

    async def plan_query(
        self,
        question: str,
        summaries: List[DocumentSummary],
        candidate_keys: List[str],
        history: List[Dict[str, str]],
        company_id: str,
    ) -> MultiDocQueryPlan:
        if self.default_plan:
            return self.default_plan

        if len(summaries) < 2:
            return MultiDocQueryPlan(
                intent="single_doc",
                target_document_ids=[s.document_id for s in summaries],
                requires_multi_doc=False,
            )

        q_lower = question.lower()
        matched_docs = [s for s in summaries if s.source_name.lower() in q_lower or s.document_id.lower() in q_lower]
        has_trigger = any(kw in q_lower for kw in MULTIDOC_TRIGGER_KEYWORDS)

        # Trigger multi-doc when >=2 docs mentioned or trigger keywords present
        if len(matched_docs) >= 2 or has_trigger:
            target_ids = [s.document_id for s in matched_docs] if len(matched_docs) >= 2 else [s.document_id for s in summaries[:2]]
            return MultiDocQueryPlan(
                intent="cross_doc_join",
                target_document_ids=target_ids,
                join_keys=candidate_keys[:1] if candidate_keys else [],
                requires_multi_doc=True,
            )

        return MultiDocQueryPlan(
            intent="single_doc",
            target_document_ids=[summaries[0].document_id],
            requires_multi_doc=False,
        )
