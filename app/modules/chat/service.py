"""
Chat Query Service (app/modules/chat/service.py)
End-to-end natural language to MongoDB pipeline with caching, scoping, and response templating.
"""

import json
import time
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.infrastructure.azure.redis_cache import build_cache_key
from app.modules.chat.interfaces import (
    AnswerWriterProtocol,
    ChatCacheProtocol,
    ChatRepoProtocol,
    QueryExecutorProtocol,
    RecordSearchRepository,
    SQLGeneratorProtocol,
)
from app.modules.chat.models import (
    create_conversation_document,
    create_message_document,
)
from app.modules.chat.response_builder import determine_chart_hint, fill_template
from app.modules.sql_guard.validator import validate_and_format_sql

RELATIVE_TIME_WORDS = ["today", "yesterday", "this week", "last month", "current date", "now"]


class QueryService:
    def __init__(
        self,
        executor: QueryExecutorProtocol,
        generator: SQLGeneratorProtocol,
        repo: ChatRepoProtocol,
        cache: ChatCacheProtocol,
        record_search_repo: Optional[RecordSearchRepository] = None,
        answer_writer: Optional[AnswerWriterProtocol] = None,
    ):
        self.executor = executor
        self.generator = generator
        self.repo = repo
        self.cache = cache
        self.record_search_repo = record_search_repo
        self.answer_writer = answer_writer

    async def answer(
        self,
        company_id: str,
        user_id: str,
        question: str,
        history: List[Dict[str, str]],
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        await self.repo.check_and_increment_rate_limits(company_id, user_id)

        catalog = await self.repo.get_schema_catalog(company_id)
        schema_version = catalog.get("schema_version", 1)
        allowed = catalog.get("allowed_collections") or catalog.get("allowed_views")

        # Pre-check: If company has no data uploaded in any dataset, refuse immediately without calling AI
        total_rows = catalog.get("total_row_count")
        if total_rows is not None and total_rows == 0:
            latency = int((time.time() - start_time) * 1000)
            return self._build_result(
                "refused",
                "No data uploaded",
                "No data has been uploaded for your company yet. Ask your admin to upload a CSV in Company data.",
                [],
                [],
                False,
                "none",
                [],
                latency,
                "miss",
            )

        is_relative = any(w in question.lower() for w in RELATIVE_TIME_WORDS)
        cache_key = build_cache_key(company_id, schema_version, question)

        cached_json = await self.cache.get(cache_key) if not is_relative else None
        if cached_json:
            try:
                cached_data = json.loads(cached_json)
                if cached_data.get("plan"):
                    from app.modules.chat.plan_translator import translate_plan_to_query
                    from app.modules.chat.query_plan import QueryPlan

                    plan_obj = QueryPlan.model_validate(cached_data["plan"])
                    translated_spec, cols = translate_plan_to_query(plan_obj, catalog)
                    norm_spec, cols = validate_and_format_sql(translated_spec, allowed)
                else:
                    q_val = cached_data.get("query") or cached_data.get("sql")
                    norm_spec, cols = validate_and_format_sql(q_val, allowed)

                col_defs, rows, trunc = await self.executor.execute_query(norm_spec, company_id, columns=cols)
                chart = determine_chart_hint(col_defs, rows)
                headline = fill_template(cached_data.get("headline_template"), col_defs, rows)
                detail = fill_template(cached_data.get("detail_template"), col_defs, rows)
                latency = int((time.time() - start_time) * 1000)
                return self._build_result("success", headline, detail, col_defs, rows, trunc, chart, cached_data.get("follow_ups", []), latency, "hit")
            except Exception:
                pass

        gen = await self.generator.generate(question, catalog, history, company_id)
        decision = gen.get("decision", "answer")

        if decision == "refuse":
            if settings.TEXT_SEARCH_FALLBACK_ENABLED and self.record_search_repo:
                search_res = await self._try_record_search(company_id, user_id, question, conversation_id, start_time)
                if search_res:
                    return search_res

            latency = int((time.time() - start_time) * 1000)
            return self._build_result("refused", "Request Refused", "This question is off-topic or violates safety rules.", [], [], False, "none", [], latency, "miss")

        if decision == "text_search":
            if self.record_search_repo:
                search_res = await self._try_record_search(company_id, user_id, question, conversation_id, start_time)
                if search_res:
                    return search_res
            latency = int((time.time() - start_time) * 1000)
            return self._build_result("refused", "No matching records found", "No company records match your search query.", [], [], False, "none", [], latency, "miss")

        if decision == "clarify":
            latency = int((time.time() - start_time) * 1000)
            return self._build_result("clarification_needed", None, None, [], [], False, "none", [], latency, "miss", gen.get("clarification_question"), gen.get("options", []))

        raw_query = gen.get("query") or gen.get("sql")
        try:
            norm_spec, cols = validate_and_format_sql(raw_query, allowed)
            col_defs, rows, trunc = await self.executor.execute_query(norm_spec, company_id, columns=cols)
        except Exception:
            try:
                repaired = await self.generator.generate(f"Fix this MongoDB query for question '{question}': {raw_query}", catalog, [], company_id)
                repair_target = repaired.get("query") or repaired.get("sql")
                norm_spec, cols = validate_and_format_sql(repair_target, allowed)
                col_defs, rows, trunc = await self.executor.execute_query(norm_spec, company_id, columns=cols)
            except Exception:
                if settings.TEXT_SEARCH_FALLBACK_ENABLED and self.record_search_repo:
                    search_res = await self._try_record_search(company_id, user_id, question, conversation_id, start_time)
                    if search_res:
                        return search_res
                latency = int((time.time() - start_time) * 1000)
                return self._build_result("refused", "Request Refused", "Unable to execute query for this question.", [], [], False, "none", [], latency, "miss")

        chart = determine_chart_hint(col_defs, rows)
        headline = fill_template(gen.get("headline_template"), col_defs, rows)
        detail = fill_template(gen.get("detail_template"), col_defs, rows)
        follow_ups = gen.get("follow_ups", [])

        if self.answer_writer and settings.ANSWER_WRITER_ENABLED:
            try:
                written = await self.answer_writer.write_answer(question, col_defs, rows)
                if written:
                    headline = written
                    detail = written
            except Exception:
                pass

        if not is_relative and norm_spec:
            payload = {
                "query": norm_spec,
                "sql": json.dumps(norm_spec),
                "plan": gen.get("plan"),
                "headline_template": gen.get("headline_template"),
                "detail_template": gen.get("detail_template"),
                "follow_ups": follow_ups,
            }
            await self.cache.set(cache_key, json.dumps(payload), ex=86400)

        await self.repo.increment_monthly_usage(company_id, tokens=150)

        if not conversation_id:
            conv_doc = create_conversation_document(company_id, user_id, title=question)
            conversation_id = await self.repo.save_conversation(conv_doc)

        latency = int((time.time() - start_time) * 1000)
        res = self._build_result("success", headline, detail, col_defs, rows, trunc, chart, follow_ups, latency, "miss")
        msg_doc = create_message_document(conversation_id, company_id, user_id, question, "success", res["answer"], res["meta"])
        await self.repo.save_message(msg_doc)
        res["conversation_id"] = conversation_id
        return res

    async def _try_record_search(
        self, company_id: str, user_id: str, question: str, conversation_id: Optional[str], start_time: float
    ) -> Optional[Dict[str, Any]]:
        if not self.record_search_repo:
            return None
        hits = await self.record_search_repo.search_records(
            company_id, question, limit=settings.TEXT_SEARCH_MAX_RESULTS
        )
        if not hits:
            return None

        cols = [
            {"name": "title", "type": "string", "friendly_name": "Title"},
            {"name": "snippet", "type": "string", "friendly_name": "Content"},
        ]
        rows = [[h["title"], h["snippet"]] for h in hits]
        headline = f"Found {len(hits)} matching record{'s' if len(hits) > 1 else ''}"
        detail = hits[0]["snippet"]
        latency = int((time.time() - start_time) * 1000)
        res = self._build_result(
            "success", headline, detail, cols, rows, False, "none", [], latency, "miss", source="records"
        )

        if not conversation_id:
            conv_doc = create_conversation_document(company_id, user_id, title=question)
            conversation_id = await self.repo.save_conversation(conv_doc)

        msg_doc = create_message_document(conversation_id, company_id, user_id, question, "success", res["answer"], res["meta"])
        await self.repo.save_message(msg_doc)
        res["conversation_id"] = conversation_id
        return res

    def _build_result(self, status, headline, detail, cols, rows, trunc, chart, follow_ups, latency, cache, clar_q=None, options=None, source=None):
        meta = {"latency_ms": latency, "cache": cache}
        if source:
            meta["source"] = source
        return {
            "status": status,
            "answer": {
                "headline": headline,
                "detail": detail,
                "summary": detail or headline or "Query processed successfully.",
                "columns": cols,
                "rows": rows,
                "row_count": len(rows),
                "truncated": trunc,
                "chart_hint": chart,
                "clarification_question": clar_q,
                "options": options or [],
            },
            "follow_ups": follow_ups,
            "meta": meta,
            "error": None,
        }

    async def list_conversations(self, user_id: str, company_id: str) -> List[Dict[str, Any]]:
        return await self.repo.list_user_conversations(user_id, company_id)

    async def submit_feedback(self, user_id: str, message_id: str, helpful: bool) -> None:
        await self.repo.save_feedback(message_id, user_id, helpful)
