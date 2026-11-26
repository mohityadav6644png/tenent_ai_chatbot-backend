"""
Unit Tests for Chat Pipeline Metrics and Instrumentation (tests/unit/test_metrics_instrumentation.py)
Verifies:
1. StageTimer context manager overhead < 1 ms.
2. Token capturing from provider usage metadata.
3. Structured metrics log emission without any text/prompts/answers/cell values.
4. Response meta fields added only when CHAT_DEBUG_METRICS=True.
5. Failing LLM calls still produce a metrics log line.
"""

import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.core.config import settings
from app.core.metrics import (
    RequestMetrics,
    clear_request_metrics,
    extract_and_record_tokens,
    get_current_metrics,
    init_request_metrics,
    measure_stage,
)
from app.modules.chat.service import QueryService


def test_stage_timer_overhead():
    metrics = init_request_metrics("test-overhead")
    # Execute 100 stage timing measurements
    import time
    t0 = time.perf_counter()
    for _ in range(100):
        with measure_stage("test_stage"):
            pass
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    per_stage_overhead_ms = elapsed_ms / 100.0
    # Must be far below 1 ms per stage (e.g., < 0.05 ms)
    assert per_stage_overhead_ms < 0.05, f"Overhead {per_stage_overhead_ms} ms too high"
    assert "test_stage" in metrics.stage_timings_ms
    clear_request_metrics()


def test_token_capture():
    metrics = init_request_metrics("test-tokens")

    fake_resp = MagicMock()
    fake_resp.usage_metadata = MagicMock()
    fake_resp.usage_metadata.prompt_token_count = 120
    fake_resp.usage_metadata.candidates_token_count = 45
    fake_resp.usage_metadata.total_token_count = 165
    fake_resp.usage_metadata.cached_content_token_count = 20
    fake_resp.usage_metadata.thinking_token_count = 10

    extract_and_record_tokens(fake_resp, "generator", "gemini-3.5-flash-lite", retries=0)

    assert "generator" in metrics.token_usage
    g = metrics.token_usage["generator"]
    assert g["model"] == "gemini-3.5-flash-lite"
    assert g["prompt_tokens"] == 120
    assert g["output_tokens"] == 45
    assert g["total_tokens"] == 165
    assert g["cached_tokens"] == 20
    assert g["thinking_tokens"] == 10
    assert g["call_count"] == 1
    clear_request_metrics()


def test_structured_log_emission_no_sensitive_text(caplog):
    metrics = init_request_metrics("test-req-123")
    metrics.stage_timings_ms["load_catalog"] = 2.5
    metrics.stage_timings_ms["generate_llm"] = 450.0
    metrics.set_catalog_stats(3, 1500)
    metrics.record_tokens("generator", "test-model", 100, 50, 150)

    from app.core.logging import logger
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="app"):
            metrics.finish(status="success", cache_status="miss")

        # Find the chat_metrics log record
        metric_records = [r for r in caplog.records if "chat_metrics" in r.getMessage()]
        assert len(metric_records) >= 1
        log_text = metric_records[0].getMessage()
        parsed = json.loads(log_text)

        assert parsed["event"] == "chat_metrics"
        assert parsed["request_id"] == "test-req-123"
        assert parsed["status"] == "success"
        assert parsed["cache"] == "miss"
        assert "total" in parsed["timings_ms"]
        assert parsed["catalog_files"] == 3
        assert parsed["catalog_chars"] == 1500

        # Ensure absolutely NO prompt, question, answer, row, or SQL text exists in the log message
        forbidden_terms = ["SELECT", "FROM", "$match", "poem", "student", "Mohit", "Priya", "question", "answer"]
        for term in forbidden_terms:
            assert term.lower() not in log_text.lower()
    finally:
        logger.removeHandler(caplog.handler)
        clear_request_metrics()


@pytest.mark.asyncio
async def test_debug_metrics_setting(monkeypatch):
    service = QueryService(
        executor=AsyncMock(),
        generator=AsyncMock(),
        repo=AsyncMock(),
        cache=AsyncMock(),
    )
    service.repo.check_and_increment_rate_limits = AsyncMock()
    service.repo.get_schema_catalog = AsyncMock(return_value={"files": [], "total_row_count": 0})
    service.repo.save_conversation = AsyncMock(return_value="conv1")
    service.repo.save_message = AsyncMock()

    # 1. When CHAT_DEBUG_METRICS=False (default)
    monkeypatch.setattr(settings, "CHAT_DEBUG_METRICS", False)
    clear_request_metrics()
    res1 = await service.answer(
        company_id="comp1", user_id="user1", question="How many doctors?", history=[]
    )
    assert "timings_ms" not in res1["meta"]
    assert "tokens" not in res1["meta"]

    # 2. When CHAT_DEBUG_METRICS=True
    monkeypatch.setattr(settings, "CHAT_DEBUG_METRICS", True)
    clear_request_metrics()
    res2 = await service.answer(
        company_id="comp1", user_id="user1", question="How many doctors?", history=[]
    )
    assert "timings_ms" in res2["meta"]
    assert "tokens" in res2["meta"]
    assert "total" in res2["meta"]["timings_ms"]
    clear_request_metrics()


@pytest.mark.asyncio
async def test_failing_llm_produces_metrics_log(monkeypatch, caplog):
    service = QueryService(
        executor=AsyncMock(),
        generator=AsyncMock(),
        repo=AsyncMock(),
        cache=AsyncMock(),
    )
    service.repo.check_and_increment_rate_limits = AsyncMock()
    service.repo.get_schema_catalog = AsyncMock(return_value={"files": [{"slug": "f1"}], "total_row_count": 10})
    service.cache.get = AsyncMock(return_value=None)
    # Generator throws an error
    from app.core.exceptions import LlmUnavailableError
    service.generator.generate = AsyncMock(side_effect=LlmUnavailableError("Service busy"))

    monkeypatch.setattr(settings, "TEXT_SEARCH_FALLBACK_ENABLED", False)

    clear_request_metrics()
    from app.core.logging import logger
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="app"):
            with pytest.raises(LlmUnavailableError):
                await service.answer(
                    company_id="comp1", user_id="user1", question="What is total fee?", history=[]
                )

        metric_records = [r for r in caplog.records if "chat_metrics" in r.getMessage()]
        assert len(metric_records) >= 1
        parsed = json.loads(metric_records[0].getMessage())
        assert parsed["status"] == "error"
    finally:
        logger.removeHandler(caplog.handler)
        clear_request_metrics()
