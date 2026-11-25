"""
Gemini Multi-document Planner (app/infrastructure/multidoc_planner/gemini_planner.py)
Hardened query planner using Google GenAI SDK with structured JSON output.
"""

import json
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import types
from app.core.config import settings
from app.core.logging import logger
from app.infrastructure.gemini.client import get_gemini_client, get_gemini_limiter
from app.infrastructure.gemini.common import clean_and_parse_json, record_tenant_token_usage
from app.infrastructure.gemini.limiter import InProcessLimiter
from app.modules.multidoc.interfaces import MultiDocPlannerProtocol
from app.modules.multidoc.models import DocumentSummary, MultiDocQueryPlan

SYSTEM_PROMPT = """You are a query planner for a multi-tenant business chatbot.
Given available document schemas and candidate join keys for the current company,
determine whether the user's question requires information from a single document or across multiple documents.
Never invent document IDs or field names.
Output strict JSON with:
{
  "intent": "single_doc" | "cross_doc_union" | "cross_doc_join" | "cross_doc_compare",
  "target_document_ids": string[],
  "join_keys": string[],
  "requires_multi_doc": boolean
}
"""


class GeminiMultiDocPlanner(MultiDocPlannerProtocol):
    def __init__(self, client: Optional[genai.Client] = None, limiter: Optional[InProcessLimiter] = None):
        api_key_obj = settings.GEMINI_API_KEY
        api_key = api_key_obj.get_secret_value() if hasattr(api_key_obj, "get_secret_value") else str(api_key_obj or "")
        model_name = settings.GEMINI_MODEL
        if not api_key or not model_name:
            raise ValueError("GEMINI_API_KEY and GEMINI_MODEL must be configured when LLM_PROVIDER=gemini")
        self.client = client or get_gemini_client()
        self.limiter = limiter or get_gemini_limiter()
        self.model_name = model_name

    async def plan_query(
        self,
        question: str,
        summaries: List[DocumentSummary],
        candidate_keys: List[str],
        history: List[Dict[str, str]],
        company_id: str,
    ) -> MultiDocQueryPlan:
        summaries_payload = [
            {"document_id": s.document_id, "name": s.source_name, "fields": s.fields}
            for s in summaries
        ]
        prompt = (
            f"Available documents:\n{json.dumps(summaries_payload)}\n"
            f"Candidate join keys: {json.dumps(candidate_keys)}\n\n"
            f"<UNTRUSTED_USER_QUERY>\n{question}\n</UNTRUSTED_USER_QUERY>\n"
        )
        try:
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
            )
            async with self.limiter.limit():
                response = await self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )
            raw_text = response.text or "{}"
            parsed = clean_and_parse_json(raw_text)
            tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0
            if tokens > 0:
                await record_tenant_token_usage(company_id, tokens)
            return MultiDocQueryPlan(
                intent=parsed.get("intent", "single_doc"),
                target_document_ids=parsed.get("target_document_ids", []),
                join_keys=parsed.get("join_keys", candidate_keys[:1] if candidate_keys else []),
                requires_multi_doc=bool(parsed.get("requires_multi_doc", False)),
            )
        except Exception:
            logger.warning("gemini_multidoc_plan_error", extra={"company_id": str(company_id)})
            raise
