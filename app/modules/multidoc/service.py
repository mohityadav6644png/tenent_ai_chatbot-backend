"""
Multi-document Service (app/modules/multidoc/service.py)
Orchestrates document discovery, join-key detection, planning, and merging.
Pure Python domain logic. No motor, redis, google-genai, or fastapi imports.
"""

import asyncio
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.logging import logger
from app.modules.multidoc.interfaces import MultiDocPlannerProtocol, MultiDocRepositoryProtocol
from app.modules.multidoc.join_detector import detect_join_keys
from app.modules.multidoc.merger import merge_document_records
from app.modules.multidoc.models import DocumentSummary, MultiDocAnswerMeta, MultiDocQueryPlan


def _deterministic_fallback(
    question: str, summaries: List[DocumentSummary], candidate_keys: List[str]
) -> Optional[MultiDocQueryPlan]:
    q_lower = question.lower()
    keywords = ["both", "across", "compare", "combine", "together", "join", "all files", "multiple files", "with"]
    matched = [s for s in summaries if s.source_name.lower() in q_lower or s.document_id.lower() in q_lower]
    if len(matched) >= 2 or (len(summaries) >= 2 and any(kw in q_lower for kw in keywords)):
        target_ids = [s.document_id for s in matched] if len(matched) >= 2 else [s.document_id for s in summaries[:2]]
        return MultiDocQueryPlan(
            intent="cross_doc_join",
            target_document_ids=target_ids,
            join_keys=candidate_keys[:1] if candidate_keys else [],
            requires_multi_doc=True,
        )
    return None


class MultiDocService:
    def __init__(
        self,
        repo: MultiDocRepositoryProtocol,
        planner: MultiDocPlannerProtocol,
        cache: Optional[Any] = None,
    ):
        self.repo = repo
        self.planner = planner
        self.cache = cache

    async def discover_documents(
        self, company_id: str, question: Optional[str] = None
    ) -> List[DocumentSummary]:
        """Load tenant-scoped document summaries capped by settings."""
        summaries = await self.repo.list_documents(
            company_id=str(company_id),
            limit=settings.MULTIDOC_MAX_DOCS_PER_QUERY,
        )
        if not question or not summaries:
            return summaries

        q_lower = question.lower()
        matched = [s for s in summaries if s.source_name.lower() in q_lower or s.document_id.lower() in q_lower]
        if len(matched) >= 2:
            return matched
        return summaries

    async def evaluate_and_execute(
        self,
        company_id: str,
        question: str,
        history: List[Dict[str, str]],
    ) -> Optional[Dict[str, Any]]:
        """Evaluate if question requires multi-doc synthesis; fetch and merge if so."""
        if not getattr(settings, "MULTIDOC_ENABLED", True):
            return None

        summaries = await self.discover_documents(company_id, question)
        if len(summaries) < 2:
            return None

        q_lower = question.lower()
        cross_doc_keywords = ["both", "across", "compare", "combine", "together", "join", "all files", "multiple files", "merged", "and also in"]
        has_cross_doc_kw = any(kw in q_lower for kw in cross_doc_keywords)
        matched = [s for s in summaries if s.source_name.lower() in q_lower or s.document_id.lower() in q_lower]

        # Fast path & cost guardrail: If question does not mention at least 2 documents and has no cross-doc keywords,
        # it is a single-document query. Skip calling the LLM planner to save 3-5s latency and eliminate API cost.
        if len(matched) < 2 and not has_cross_doc_kw:
            return None

        candidate_keys = detect_join_keys([s.fields for s in summaries])
        if not candidate_keys:
            return None

        # Step: LLM / Rule planning with timeout
        plan: Optional[MultiDocQueryPlan] = None
        try:
            plan = await asyncio.wait_for(
                self.planner.plan_query(
                    question=question,
                    summaries=summaries,
                    candidate_keys=candidate_keys,
                    history=history,
                    company_id=str(company_id),
                ),
                timeout=settings.MULTIDOC_PLAN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("multidoc_plan_timeout", extra={"company_id": str(company_id)})
            plan = _deterministic_fallback(question, summaries, candidate_keys)
        except Exception:
            logger.warning("multidoc_plan_error", extra={"company_id": str(company_id)})
            plan = _deterministic_fallback(question, summaries, candidate_keys)

        if not plan or not plan.requires_multi_doc or plan.intent == "single_doc":
            return None

        # Resolve target documents
        target_ids = set(plan.target_document_ids) if plan.target_document_ids else {s.document_id for s in summaries}
        target_docs = [s for s in summaries if s.document_id in target_ids]
        if len(target_docs) < 2:
            target_docs = summaries[:2]

        join_keys = plan.join_keys if plan.join_keys else candidate_keys

        # Fetch records scoped strictly by company_id
        doc_records: Dict[str, List[Dict[str, Any]]] = {}
        doc_names: Dict[str, str] = {}
        for d in target_docs:
            doc_names[d.document_id] = d.source_name
            records = await self.repo.get_document_records(
                company_id=str(company_id),
                document_id=d.document_id,
                limit=settings.MULTIDOC_MAX_RECORDS_PER_DOC,
            )
            doc_records[d.document_id] = records

        total_records = sum(len(r) for r in doc_records.values())
        if total_records == 0:
            return None

        col_defs, rows, truncated = merge_document_records(
            doc_records=doc_records,
            doc_names=doc_names,
            join_keys=join_keys,
            max_merged_rows=settings.MULTIDOC_MAX_MERGED_ROWS,
        )

        multidoc_meta = MultiDocAnswerMeta(
            used_multi_doc=True,
            document_ids=[d.document_id for d in target_docs],
            source_names=[d.source_name for d in target_docs],
            join_keys_used=join_keys,
            partial_coverage_note=None,
        )

        return {
            "col_defs": col_defs,
            "rows": rows,
            "truncated": truncated,
            "multidoc_meta": multidoc_meta,
            "target_docs": target_docs,
            "plan": plan,
        }
