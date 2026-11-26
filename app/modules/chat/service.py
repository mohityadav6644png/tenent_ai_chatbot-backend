"""
Chat Query Service (app/modules/chat/service.py)
End-to-end natural language to MongoDB pipeline with caching, scoping, and response templating.
"""

import json
import time
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.infrastructure.azure.redis_cache import build_cache_key
from app.modules.chat.followup import FollowUpService
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
        followup_service: Optional[FollowUpService] = None,
        profile_service: Optional[Any] = None,
        multidoc_service: Optional[Any] = None,
    ):
        self.executor = executor
        self.generator = generator
        self.repo = repo
        self.cache = cache
        self.record_search_repo = record_search_repo
        self.answer_writer = answer_writer
        self.followup_service = followup_service
        self.profile_service = profile_service
        self.multidoc_service = multidoc_service

    def _finalize_result(self, res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.metrics import get_current_metrics
        metrics = get_current_metrics()
        if metrics:
            status = res.get("status", "success")
            cache_status = res.get("meta", {}).get("cache", "miss")
            metrics.finish(status=status, cache_status=cache_status)
            if getattr(settings, "CHAT_DEBUG_METRICS", False):
                res["meta"]["timings_ms"] = metrics.stage_timings_ms
                res["meta"]["tokens"] = metrics.token_usage
                res["meta"]["catalog_chars"] = metrics.catalog_chars
        return res

    async def _attach_profile_prompt(
        self,
        res: Dict[str, Any],
        company_id: str,
        user_id: str,
        conversation_id: Optional[str],
        profile: Optional[Any],
    ) -> Dict[str, Any]:
        if not self.profile_service or not profile or not conversation_id:
            res["profile_prompt"] = None
            return self._finalize_result(res)
        is_success = bool(res.get("status") == "success")
        prompt = await self.profile_service.post_turn_prompt(
            company_id=company_id,
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            is_success=is_success,
        )
        res["profile_prompt"] = prompt
        return self._finalize_result(res)

    async def answer(
        self,
        company_id: str,
        user_id: str,
        question: str,
        history: List[Dict[str, str]],
        conversation_id: Optional[str] = None,
        background_tasks: Optional[Any] = None,
    ) -> Dict[str, Any]:
        from app.core.metrics import get_current_metrics, init_request_metrics
        metrics = get_current_metrics()
        if not metrics:
            metrics = init_request_metrics("internal")
            metrics.stage_timings_ms["auth_session"] = 0.1
        try:
            return await self._answer_internal(
                company_id=company_id,
                user_id=user_id,
                question=question,
                history=history,
                conversation_id=conversation_id,
                background_tasks=background_tasks,
            )
        except Exception:
            metrics.finish(status="error", cache_status="miss")
            raise

    async def _answer_internal(
        self,
        company_id: str,
        user_id: str,
        question: str,
        history: List[Dict[str, str]],
        conversation_id: Optional[str] = None,
        background_tasks: Optional[Any] = None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        from app.core.metrics import measure_stage, get_current_metrics
        metrics = get_current_metrics()
        with measure_stage("audit"):
            await self.repo.check_and_increment_rate_limits(company_id, user_id)

        # 2. Progressive profiling check
        profile = None
        with measure_stage("profile_extract"):
            if self.profile_service:
                profile = await self.profile_service.get_or_init_profile(company_id, user_id)
                from app.modules.profile.sanitizer import matches_phrase
                if matches_phrase(question, settings.PROFILE_OPT_OUT_PHRASES):
                    await self.profile_service.repo.set_opted_out(company_id, user_id)
                    if conversation_id:
                        st = await self.profile_service.get_session_state(company_id, user_id, conversation_id)
                        st.pending_field = None
                        await self.profile_service.repo.save_session_state(st)
                    latency = int((time.time() - start_time) * 1000)
                    res = self._build_result(
                        "success", "Understood, I won't ask you again.", "", [], [], False, "none", [], latency, "miss"
                    )
                    res["conversation_id"] = conversation_id
                    res["profile_prompt"] = None
                    msg_doc = create_message_document(
                        conversation_id, company_id, user_id, question, "success",
                        res["answer"], res["meta"], resolved_question=question, original_text=question
                    )
                    await self.repo.save_message(msg_doc)
                    return self._finalize_result(res)

                if conversation_id:
                    state = await self.profile_service.get_session_state(company_id, user_id, conversation_id)
                    if state.pending_field:
                        ack_headline, remaining_q = await self.profile_service.handle_pending_turn(
                            company_id, user_id, conversation_id, question, profile, state
                        )
                        if ack_headline:
                            latency = int((time.time() - start_time) * 1000)
                            res = self._build_result(
                                "success", ack_headline, "", [], [], False, "none", [], latency, "miss"
                            )
                            res["conversation_id"] = conversation_id
                            res["profile_prompt"] = None
                            msg_doc = create_message_document(
                                conversation_id, company_id, user_id, question, "success",
                                res["answer"], res["meta"], resolved_question=question, original_text=question
                            )
                            await self.repo.save_message(msg_doc)
                            return self._finalize_result(res)
                        if remaining_q:
                            question = remaining_q
                if self.profile_service.matches_trigger(question):
                    can_extract = any(f.value is None or f.status != "collected" for f in profile.fields.values())
                    if can_extract:
                        try:
                            ext_res, profile, validated = await self.profile_service.extract_and_merge(company_id, user_id, question)
                            if ext_res and validated:
                                if ext_res.message_kind == "profile_answer":
                                    known_name = profile.fields.get("name", {}).value if profile.fields.get("name") else None
                                    headline = f"Thanks {known_name}, noted." if (known_name and isinstance(known_name, str)) else "Thanks, noted."
                                    latency = int((time.time() - start_time) * 1000)
                                    res = self._build_result(
                                        "success", headline, "", [], [], False, "none", [], latency, "miss"
                                    )
                                    res["conversation_id"] = conversation_id
                                    res["profile_prompt"] = None
                                    msg_doc = create_message_document(
                                        conversation_id, company_id, user_id, question, "success",
                                        res["answer"], res["meta"], resolved_question=question, original_text=question
                                    )
                                    await self.repo.save_message(msg_doc)
                                    return self._finalize_result(res)
                                elif ext_res.message_kind == "both" and ext_res.remaining_question:
                                    question = ext_res.remaining_question
                        except Exception as exc:
                            logger.warning(f"profile_extraction_error: {type(exc).__name__}")

        # 1a. Server-side context: validate conversation ownership and load previous turns
        turns: List[Dict[str, Any]] = []
        with measure_stage("load_history"):
            if conversation_id:
                conv = await self.repo.get_conversation(conversation_id, company_id, user_id)
                if not conv:
                    raise NotFoundError("Conversation not found")
                turns = await self.repo.get_conversation_messages(
                    conversation_id=conversation_id,
                    company_id=company_id,
                    user_id=user_id,
                    limit=settings.FOLLOWUP_MAX_TURNS,
                    ttl_hours=settings.CONVERSATION_CONTEXT_TTL_HOURS,
                )

        with measure_stage("load_catalog"):
            catalog = await self.repo.get_schema_catalog(company_id)
            if metrics:
                cat_files = len(catalog.get("files", []))
                cat_chars = len(json.dumps(catalog, default=str))
                metrics.set_catalog_stats(cat_files, cat_chars)
        schema_version = catalog.get("schema_version", 1)
        allowed = catalog.get("allowed_collections") or catalog.get("allowed_views")
        file_fields = catalog.get("file_fields") or {}
        allowed_set = set(allowed) if allowed else None

        # 1c. Follow-up resolution
        followup_res = (
            await self.followup_service.resolve(question, turns, catalog, company_id)
            if self.followup_service
            else {
                "is_followup": False,
                "standalone_question": question,
                "needs_clarification": False,
                "clarification_question": None,
            }
        )
        is_followup = followup_res.get("is_followup", False)
        standalone_question = followup_res.get("standalone_question", question)
        needs_clarification = followup_res.get("needs_clarification", False)
        clarification_question = followup_res.get("clarification_question")
        is_rewritten = bool(is_followup and (standalone_question.strip().lower() != question.strip().lower()))

        # 1d. If resolver indicates clarification is needed (e.g. positional reference), return immediately
        if needs_clarification:
            latency = int((time.time() - start_time) * 1000)
            if not conversation_id:
                conv_doc = create_conversation_document(company_id, user_id, title=question)
                conversation_id = await self.repo.save_conversation(conv_doc)
            res = self._build_result(
                "clarification_needed", None, None, [], [], False, "none", [], latency, "miss",
                clar_q=clarification_question, options=[]
            )
            res["conversation_id"] = conversation_id
            msg_doc = create_message_document(
                conversation_id, company_id, user_id, question, "clarification_needed",
                res["answer"], res["meta"], resolved_question=standalone_question, original_text=question
            )
            await self.repo.save_message(msg_doc)
            return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

        effective_question = standalone_question if is_followup else question

        # Pre-check: If company has no ready files (or no data uploaded), refuse immediately without calling AI
        ready_files = catalog.get("files")
        total_rows = catalog.get("total_row_count")
        if (ready_files is not None and len(ready_files) == 0 and (total_rows is None or total_rows == 0)) or (
            total_rows is not None and total_rows == 0 and not ready_files
        ):
            latency = int((time.time() - start_time) * 1000)
            if not conversation_id:
                conv_doc = create_conversation_document(company_id, user_id, title=question)
                conversation_id = await self.repo.save_conversation(conv_doc)
            res = self._build_result(
                "refused",
                "No data uploaded",
                "No data has been uploaded for your company yet. Ask your admin to add a CSV file in Company data.",
                [],
                [],
                False,
                "none",
                [],
                latency,
                "miss",
            )
            if is_rewritten:
                res["meta"]["interpreted_question"] = effective_question
            res["conversation_id"] = conversation_id
            msg_doc = create_message_document(
                conversation_id, company_id, user_id, question, "refused",
                res["answer"], res["meta"], resolved_question=effective_question,
                refusal_reason="No data uploaded", original_text=question
            )
            await self.repo.save_message(msg_doc)
            return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

        # Multi-document querying check
        if self.multidoc_service and getattr(settings, "MULTIDOC_ENABLED", True):
            try:
                from app.core.cache import cache_get, cache_set, hash_question
                md_cache_key = f"{schema_version}:{hash_question(effective_question)}"
                cached_md = await cache_get(company_id, "multidoc", md_cache_key)
                if cached_md and isinstance(cached_md, dict):
                    col_defs = cached_md.get("col_defs", [])
                    rows = cached_md.get("rows", [])
                    trunc = cached_md.get("truncated", False)
                    multidoc_meta = cached_md.get("multidoc_meta")
                    headline = cached_md.get("headline", "")
                    detail = cached_md.get("detail", "")
                    follow_ups = cached_md.get("follow_ups", [])
                    latency = int((time.time() - start_time) * 1000)
                    if not conversation_id:
                        conv_doc = create_conversation_document(company_id, user_id, title=question)
                        conversation_id = await self.repo.save_conversation(conv_doc)
                    res = self._build_result(
                        "success", headline, detail, col_defs, rows, trunc, "none", follow_ups, latency, "hit", multidoc_meta=multidoc_meta
                    )
                    if is_rewritten:
                        res["meta"]["interpreted_question"] = effective_question
                    res["conversation_id"] = conversation_id
                    col_names = [c["name"] for c in col_defs] if col_defs else []
                    msg_doc = create_message_document(
                        conversation_id, company_id, user_id, question, "success",
                        res["answer"], res["meta"], resolved_question=effective_question,
                        file_slug=None, output_columns=col_names, original_text=question
                    )
                    await self.repo.save_message(msg_doc)
                    return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

                md_res = await self.multidoc_service.evaluate_and_execute(company_id, effective_question, history)
                if md_res:
                    col_defs = md_res["col_defs"]
                    rows = md_res["rows"]
                    trunc = md_res["truncated"]
                    multidoc_meta = md_res["multidoc_meta"]
                    sources_str = ", ".join(multidoc_meta.source_names)
                    chart = determine_chart_hint(col_defs, rows)
                    headline = f"Synthesized data across {sources_str}"
                    detail = f"Found {len(rows)} matching record{'s' if len(rows) != 1 else ''} combined from {sources_str}."
                    if self.answer_writer and settings.ANSWER_WRITER_ENABLED:
                        try:
                            written = await self.answer_writer.write_answer(effective_question, col_defs, rows)
                            if written:
                                headline = written
                                detail = written
                        except Exception:
                            pass
                    follow_ups = [f"Break down by {k}" for k in multidoc_meta.join_keys_used[:2]]
                    latency = int((time.time() - start_time) * 1000)
                    if not conversation_id:
                        conv_doc = create_conversation_document(company_id, user_id, title=question)
                        conversation_id = await self.repo.save_conversation(conv_doc)
                    meta_dict = multidoc_meta.model_dump() if hasattr(multidoc_meta, "model_dump") else multidoc_meta
                    res = self._build_result(
                        "success", headline, detail, col_defs, rows, trunc, chart, follow_ups, latency, "miss", multidoc_meta=meta_dict
                    )
                    if is_rewritten:
                        res["meta"]["interpreted_question"] = effective_question
                    res["conversation_id"] = conversation_id
                    col_names = [c["name"] for c in col_defs] if col_defs else []
                    msg_doc = create_message_document(
                        conversation_id, company_id, user_id, question, "success",
                        res["answer"], res["meta"], resolved_question=effective_question,
                        file_slug=None, output_columns=col_names, original_text=question
                    )
                    await self.repo.save_message(msg_doc)
                    await cache_set(
                        company_id, "multidoc", md_cache_key,
                        {
                            "col_defs": col_defs,
                            "rows": rows,
                            "truncated": trunc,
                            "multidoc_meta": meta_dict,
                            "headline": headline,
                            "detail": detail,
                            "follow_ups": follow_ups,
                        },
                        ttl_seconds=getattr(settings, "MULTIDOC_CACHE_TTL_SECONDS", 120),
                    )
                    return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)
            except Exception:
                pass

        is_relative = any(w in effective_question.lower() for w in RELATIVE_TIME_WORDS)
        cache_key = build_cache_key(company_id, schema_version, effective_question)

        with measure_stage("cache_lookup"):
            cached_json = await self.cache.get(cache_key) if not is_relative else None
        if cached_json:
            try:
                cached_data = json.loads(cached_json)
                target_slug = None
                with measure_stage("validate"):
                    if cached_data.get("plan"):
                        from app.modules.chat.plan_translator import translate_plan_to_query
                        from app.modules.chat.query_plan import QueryPlan

                        plan_obj = QueryPlan.model_validate(cached_data["plan"])
                        target_slug = plan_obj.collection
                        translated_spec, cols = translate_plan_to_query(plan_obj, catalog)
                        norm_spec, cols = validate_and_format_sql(
                            translated_spec,
                            allowed_set,
                            catalog_fields=file_fields,
                            connections=catalog.get("connections", []),
                            company_id=company_id,
                        )
                    else:
                        q_val = cached_data.get("query") or cached_data.get("sql")
                        norm_spec, cols = validate_and_format_sql(
                            q_val,
                            allowed_set,
                            catalog_fields=file_fields,
                            connections=catalog.get("connections", []),
                            company_id=company_id,
                        )
                        if isinstance(norm_spec, dict):
                            target_slug = norm_spec.get("collection")

                with measure_stage("execute_db"):
                    col_defs, rows, trunc = await self.executor.execute_query(norm_spec, company_id, columns=cols)
                with measure_stage("build_response"):
                    chart = determine_chart_hint(col_defs, rows)
                    headline = fill_template(cached_data.get("headline_template"), col_defs, rows)
                    detail = fill_template(cached_data.get("detail_template"), col_defs, rows)
                latency = int((time.time() - start_time) * 1000)

                cached_files_used = None
                if target_slug:
                    u_files = []
                    f_m = next((f for f in catalog.get("files", []) if f.get("slug") == target_slug), None)
                    if f_m:
                        u_files.append(f_m.get("name") or f_m.get("slug"))
                    if isinstance(norm_spec, dict) and norm_spec.get("joins"):
                        for j_id in norm_spec["joins"]:
                            c_m = next((c for c in catalog.get("connections", []) if str(c.get("id")) == str(j_id)), None)
                            if c_m and c_m.get("right_file_name"):
                                u_files.append(c_m["right_file_name"])
                    if u_files:
                        cached_files_used = u_files

                with measure_stage("persist"):
                    if not conversation_id:
                        conv_doc = create_conversation_document(company_id, user_id, title=question)
                        conversation_id = await self.repo.save_conversation(conv_doc)

                    res = self._build_result(
                        "success", headline, detail, col_defs, rows, trunc, chart,
                        cached_data.get("follow_ups", []), latency, "hit", files_used=cached_files_used
                    )
                    if is_rewritten:
                        res["meta"]["interpreted_question"] = effective_question
                    col_names = [c["name"] for c in col_defs] if col_defs else []
                    msg_doc = create_message_document(
                        conversation_id, company_id, user_id, question, "success",
                        res["answer"], res["meta"], resolved_question=effective_question,
                        file_slug=target_slug, output_columns=col_names, original_text=question
                    )
                    await self.repo.save_message(msg_doc)
                res["conversation_id"] = conversation_id
                return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)
            except Exception:
                pass

        with measure_stage("generate_llm"):
            try:
                gen = await self.generator.generate(effective_question, catalog, [], company_id)
            except Exception as gen_exc:
                from app.core.exceptions import LlmUnavailableError
                if isinstance(gen_exc, LlmUnavailableError):
                    if settings.TEXT_SEARCH_FALLBACK_ENABLED and self.record_search_repo:
                        search_res = await self._try_record_search(company_id, user_id, effective_question, conversation_id, start_time)
                        if search_res:
                            if is_rewritten:
                                search_res["meta"]["interpreted_question"] = effective_question
                            return await self._attach_profile_prompt(search_res, company_id, user_id, conversation_id, profile)
                    raise gen_exc
                logger.warning(f"Generator error: {type(gen_exc).__name__}: {gen_exc}")
                if settings.TEXT_SEARCH_FALLBACK_ENABLED and self.record_search_repo:
                    search_res = await self._try_record_search(company_id, user_id, effective_question, conversation_id, start_time)
                    if search_res:
                        if is_rewritten:
                            search_res["meta"]["interpreted_question"] = effective_question
                        return await self._attach_profile_prompt(search_res, company_id, user_id, conversation_id, profile)
                raise LlmUnavailableError("The AI service is busy. Please try again in a moment.")
        decision = gen.get("decision", "answer")

        if decision == "refuse":
            if gen.get("refusal_reason") == "no_connection":
                latency = int((time.time() - start_time) * 1000)
                if not conversation_id:
                    conv_doc = create_conversation_document(company_id, user_id, title=question)
                    conversation_id = await self.repo.save_conversation(conv_doc)
                no_conn_msg = "These files aren't connected yet. Ask your admin to connect them in Company data."
                try:
                    from app.infrastructure.db.mongodb import file_connections_collection
                    suggested = await file_connections_collection.find_one({"company_id": str(company_id), "status": "suggested"})
                    if suggested:
                        files = catalog.get("files", [])
                        l_f = next((f for f in files if str(f.get("_id") or f.get("id")) == str(suggested.get("left_file_id"))), None)
                        r_f = next((f for f in files if str(f.get("_id") or f.get("id")) == str(suggested.get("right_file_id"))), None)
                        if l_f and r_f:
                            l_name = l_f.get("name") or l_f.get("slug")
                            r_name = r_f.get("name") or r_f.get("slug")
                            no_conn_msg = f"A connection between {l_name} and {r_name} is waiting for your admin's approval."
                except Exception:
                    pass
                res = self._build_result(
                    "refused",
                    "These files aren't connected yet.",
                    no_conn_msg,
                    [],
                    [],
                    False,
                    "none",
                    [],
                    latency,
                    "miss",
                    refusal_reason="no_connection",
                )
                if is_rewritten:
                    res["meta"]["interpreted_question"] = effective_question
                msg_doc = create_message_document(
                    conversation_id, company_id, user_id, question, "refused",
                    res["answer"], res["meta"], resolved_question=effective_question,
                    refusal_reason="no_connection", original_text=question
                )
                await self.repo.save_message(msg_doc)
                res["conversation_id"] = conversation_id
                return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

            if settings.TEXT_SEARCH_FALLBACK_ENABLED and self.record_search_repo:
                search_res = await self._try_record_search(company_id, user_id, effective_question, conversation_id, start_time)
                if search_res:
                    if is_rewritten:
                        search_res["meta"]["interpreted_question"] = effective_question
                    return await self._attach_profile_prompt(search_res, company_id, user_id, conversation_id, profile)

            latency = int((time.time() - start_time) * 1000)
            if not conversation_id:
                conv_doc = create_conversation_document(company_id, user_id, title=question)
                conversation_id = await self.repo.save_conversation(conv_doc)
            res = self._build_result("refused", "Request Refused", "This question is off-topic or violates safety rules.", [], [], False, "none", [], latency, "miss")
            if is_rewritten:
                res["meta"]["interpreted_question"] = effective_question
            msg_doc = create_message_document(
                conversation_id, company_id, user_id, question, "refused",
                res["answer"], res["meta"], resolved_question=effective_question,
                refusal_reason="Request Refused", original_text=question
            )
            await self.repo.save_message(msg_doc)
            res["conversation_id"] = conversation_id
            return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

        if decision == "text_search":
            if self.record_search_repo:
                search_res = await self._try_record_search(company_id, user_id, effective_question, conversation_id, start_time)
                if search_res:
                    if is_rewritten:
                        search_res["meta"]["interpreted_question"] = effective_question
                    return await self._attach_profile_prompt(search_res, company_id, user_id, conversation_id, profile)
            latency = int((time.time() - start_time) * 1000)
            if not conversation_id:
                conv_doc = create_conversation_document(company_id, user_id, title=question)
                conversation_id = await self.repo.save_conversation(conv_doc)
            res = self._build_result("refused", "No matching records found", "No company records match your search query.", [], [], False, "none", [], latency, "miss")
            if is_rewritten:
                res["meta"]["interpreted_question"] = effective_question
            msg_doc = create_message_document(
                conversation_id, company_id, user_id, question, "refused",
                res["answer"], res["meta"], resolved_question=effective_question,
                refusal_reason="No matching records found", original_text=question
            )
            await self.repo.save_message(msg_doc)
            res["conversation_id"] = conversation_id
            return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

        if decision == "clarify":
            latency = int((time.time() - start_time) * 1000)
            if not conversation_id:
                conv_doc = create_conversation_document(company_id, user_id, title=question)
                conversation_id = await self.repo.save_conversation(conv_doc)
            res = self._build_result(
                "clarification_needed", None, None, [], [], False, "none", [], latency, "miss",
                gen.get("clarification_question"), gen.get("options", [])
            )
            if is_rewritten:
                res["meta"]["interpreted_question"] = effective_question
            msg_doc = create_message_document(
                conversation_id, company_id, user_id, question, "clarification_needed",
                res["answer"], res["meta"], resolved_question=effective_question, original_text=question
            )
            await self.repo.save_message(msg_doc)
            res["conversation_id"] = conversation_id
            return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

        # Multi-part query handling (Phase 4)
        active_conns = catalog.get("connections", [])
        gen_parts = gen.get("parts")
        if (
            gen_parts
            and isinstance(gen_parts, list)
            and getattr(settings, "MULTI_PART_ENABLED", True)
        ):
            max_parts = getattr(settings, "MULTI_PART_MAX_PARTS", 3)
            active_parts = gen_parts[:max_parts]
            part_results = []
            files_used_set = set()
            try:
                for idx, p in enumerate(active_parts):
                    p_norm, p_cols = validate_and_format_sql(
                        p,
                        allowed_set,
                        catalog_fields=file_fields,
                        connections=active_conns,
                        company_id=company_id,
                    )
                    p_col_defs, p_rows, p_trunc = await self.executor.execute_query(p_norm, company_id, columns=p_cols)
                    p_chart = determine_chart_hint(p_col_defs, p_rows)
                    part_results.append({
                        "title": p.get("title", f"Part {idx + 1}"),
                        "columns": p_col_defs,
                        "rows": p_rows,
                        "row_count": len(p_rows),
                        "chart_hint": p_chart,
                    })
                    p_coll = p_norm.get("collection")
                    p_file = next((f for f in catalog.get("files", []) if f.get("slug") == p_coll), None)
                    if p_file:
                        files_used_set.add(p_file.get("name") or p_file.get("slug"))
                    for j_id in p_norm.get("joins", []):
                        j_conn = next((c for c in active_conns if str(c.get("id")) == str(j_id)), None)
                        if j_conn and j_conn.get("right_file_name"):
                            files_used_set.add(j_conn["right_file_name"])

                first = part_results[0]
                headline = gen.get("headline") or f"Answer contains {len(part_results)} parts."
                detail = gen.get("detail") or headline
                latency = int((time.time() - start_time) * 1000)
                if not conversation_id:
                    conv_doc = create_conversation_document(company_id, user_id, title=question)
                    conversation_id = await self.repo.save_conversation(conv_doc)
                res = self._build_result(
                    "success",
                    headline,
                    detail,
                    first["columns"],
                    first["rows"],
                    False,
                    first["chart_hint"],
                    gen.get("follow_ups", []),
                    latency,
                    "miss",
                    files_used=sorted(list(files_used_set)) if files_used_set else None,
                    parts=part_results,
                )
                if is_rewritten:
                    res["meta"]["interpreted_question"] = effective_question
                col_names = [c["name"] for c in first["columns"]] if first["columns"] else []
                msg_doc = create_message_document(
                    conversation_id, company_id, user_id, question, "success",
                    res["answer"], res["meta"], resolved_question=effective_question,
                    file_slug=active_parts[0].get("collection"), output_columns=col_names, original_text=question
                )
                await self.repo.save_message(msg_doc)
                res["conversation_id"] = conversation_id
                return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)
            except Exception:
                pass

        raw_query = gen.get("query") or gen.get("sql")
        norm_spec = None
        cols = None
        col_defs = []
        rows = []
        trunc = False
        try:
            with measure_stage("validate"):
                norm_spec, cols = validate_and_format_sql(
                    raw_query, allowed_set, catalog_fields=file_fields, connections=active_conns, company_id=company_id
                )
            with measure_stage("execute_db"):
                col_defs, rows, trunc = await self.executor.execute_query(norm_spec, company_id, columns=cols)
        except Exception:
            try:
                repaired = await self.generator.generate(
                    f"Fix this MongoDB query for question '{effective_question}': {raw_query}", catalog, [], company_id
                )
                repair_target = repaired.get("query") or repaired.get("sql")
                with measure_stage("validate"):
                    norm_spec, cols = validate_and_format_sql(
                        repair_target, allowed_set, catalog_fields=file_fields, connections=active_conns, company_id=company_id
                    )
                with measure_stage("execute_db"):
                    col_defs, rows, trunc = await self.executor.execute_query(norm_spec, company_id, columns=cols)
            except Exception:
                if settings.TEXT_SEARCH_FALLBACK_ENABLED and self.record_search_repo:
                    search_res = await self._try_record_search(company_id, user_id, effective_question, conversation_id, start_time)
                    if search_res:
                        if is_rewritten:
                            search_res["meta"]["interpreted_question"] = effective_question
                        return await self._attach_profile_prompt(search_res, company_id, user_id, conversation_id, profile)
                latency = int((time.time() - start_time) * 1000)
                if not conversation_id:
                    conv_doc = create_conversation_document(company_id, user_id, title=question)
                    conversation_id = await self.repo.save_conversation(conv_doc)
                res = self._build_result("refused", "Request Refused", "Unable to execute query for this question.", [], [], False, "none", [], latency, "miss")
                if is_rewritten:
                    res["meta"]["interpreted_question"] = effective_question
                msg_doc = create_message_document(
                    conversation_id, company_id, user_id, question, "refused",
                    res["answer"], res["meta"], resolved_question=effective_question,
                    refusal_reason="Unable to execute query", original_text=question
                )
                await self.repo.save_message(msg_doc)
                res["conversation_id"] = conversation_id
                return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

        with measure_stage("build_response"):
            chart = determine_chart_hint(col_defs, rows)
            headline = fill_template(gen.get("headline_template"), col_defs, rows)
            detail = fill_template(gen.get("detail_template"), col_defs, rows)
            follow_ups = gen.get("follow_ups", [])

            if self.answer_writer and settings.ANSWER_WRITER_ENABLED:
                try:
                    written = await self.answer_writer.write_answer(effective_question, col_defs, rows)
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

        with measure_stage("audit"):
            await self.repo.increment_monthly_usage(company_id, tokens=150)

        target_slug = norm_spec.get("collection") if isinstance(norm_spec, dict) else (gen.get("plan", {}).get("collection") if isinstance(gen.get("plan"), dict) else None)
        col_names = [c["name"] for c in col_defs] if col_defs else []

        files_used_list = None
        if target_slug:
            used = []
            f_match = next((f for f in catalog.get("files", []) if f.get("slug") == target_slug), None)
            if f_match:
                used.append(f_match.get("name") or f_match.get("slug"))
            if isinstance(norm_spec, dict) and norm_spec.get("joins"):
                for j_id in norm_spec["joins"]:
                    c_match = next((c for c in active_conns if str(c.get("id")) == str(j_id)), None)
                    if c_match and c_match.get("right_file_name"):
                        used.append(c_match["right_file_name"])
            if used:
                files_used_list = used

        with measure_stage("persist"):
            if not conversation_id:
                conv_doc = create_conversation_document(company_id, user_id, title=question)
                conversation_id = await self.repo.save_conversation(conv_doc)

            latency = int((time.time() - start_time) * 1000)
            res = self._build_result(
                "success", headline, detail, col_defs, rows, trunc, chart, follow_ups, latency, "miss", files_used=files_used_list
            )
            if is_rewritten:
                res["meta"]["interpreted_question"] = effective_question
            msg_doc = create_message_document(
                conversation_id, company_id, user_id, question, "success",
                res["answer"], res["meta"], resolved_question=effective_question,
                file_slug=target_slug, output_columns=col_names, original_text=question
            )
            await self.repo.save_message(msg_doc)
        res["conversation_id"] = conversation_id
        return await self._attach_profile_prompt(res, company_id, user_id, conversation_id, profile)

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

    def _build_result(self, status, headline, detail, cols, rows, trunc, chart, follow_ups, latency, cache, clar_q=None, options=None, source=None, multidoc_meta=None, refusal_reason=None, files_used=None, parts=None):
        meta = {"latency_ms": latency, "cache": cache}
        if source:
            meta["source"] = source
        if files_used is not None:
            meta["files_used"] = files_used
        ans = {
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
        }
        if refusal_reason is not None:
            ans["refusal_reason"] = refusal_reason
        if parts is not None:
            ans["parts"] = parts
        res = {
            "status": status,
            "answer": ans,
            "follow_ups": follow_ups,
            "meta": meta,
            "error": None,
            "multidoc_meta": multidoc_meta,
        }
        if refusal_reason is not None:
            res["refusal_reason"] = refusal_reason
        return res

    async def list_conversations(self, user_id: str, company_id: str) -> List[Dict[str, Any]]:
        return await self.repo.list_user_conversations(user_id, company_id)
