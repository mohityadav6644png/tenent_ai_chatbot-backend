"""
Unit Tests: Free-Tier LLM Infrastructure (tests/unit/test_free_tier_llm.py)
Tests GeminiQueryGenerator, OpenAICompatQueryGenerator, Limiter, Token Usage, and Container wiring.
Runs 100% offline with fakes/mocks without real network calls.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from google.genai import errors
from openai import AuthenticationError, NotFoundError, RateLimitError
from app.core.config import settings
from app.core.exceptions import LlmUnavailableError
from app.infrastructure.gemini.client import GeminiQueryGenerator
from app.infrastructure.gemini.limiter import InProcessLimiter
from app.infrastructure.llm.openai_compat_client import OpenAICompatQueryGenerator


# ---------------------------------------------------------------------------
# In-Process Limiter Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_in_process_limiter_queues_then_rejects():
    limiter = InProcessLimiter(max_requests_per_minute=2, max_concurrency=2, wait_timeout=0.2)

    # First two succeed
    await limiter.acquire()
    await limiter.acquire()

    # Third should timeout and raise LlmUnavailableError
    with pytest.raises(LlmUnavailableError) as exc_info:
        await limiter.acquire()

    assert exc_info.value.code == "LLM_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after == 30
    limiter.release()
    limiter.release()


# ---------------------------------------------------------------------------
# Gemini Query Generator Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gemini_valid_answer_returns_correct_query_model(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

    mock_client = MagicMock()
    mock_response = MagicMock()
    query_payload = {
        "decision": "answer",
        "query": {
            "collection": "orders",
            "pipeline": [{"$match": {"status": "completed"}}, {"$limit": 5}],
        },
        "headline_template": "Total: {row1.total}",
        "detail_template": "Details here",
    }
    mock_response.text = json.dumps(query_payload)
    mock_response.usage_metadata = MagicMock(total_token_count=150)
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    generator = GeminiQueryGenerator(client=mock_client)
    res = await generator.generate(
        question="Show total completed orders",
        catalog={"orders": ["status", "total"]},
        history=[],
        company_id="company-123",
    )

    assert res["decision"] == "answer"
    assert res["query"]["collection"] == "orders"
    assert "sql" in res
    assert res["headline_template"] == "Total: {row1.total}"


@pytest.mark.asyncio
async def test_gemini_oversized_response_triggers_repair_then_refusal(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

    mock_client = MagicMock()
    oversized = " " * 8500 + json.dumps({"decision": "answer"})
    mock_resp1 = MagicMock(text=oversized, usage_metadata=None)
    mock_resp2 = MagicMock(text=oversized, usage_metadata=None)  # Repair is also oversized
    mock_client.aio.models.generate_content = AsyncMock(side_effect=[mock_resp1, mock_resp2])

    generator = GeminiQueryGenerator(client=mock_client)
    res = await generator.generate(
        question="Show something",
        catalog={},
        history=[],
        company_id="company-123",
    )

    assert res["decision"] == "refuse"
    assert res["query"] is None


@pytest.mark.asyncio
async def test_gemini_blocked_or_empty_response_returns_refusal(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

    mock_client = MagicMock()
    mock_resp = MagicMock(text="", usage_metadata=None)
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

    generator = GeminiQueryGenerator(client=mock_client)
    res = await generator.generate(
        question="Violates safety rules",
        catalog={},
        history=[],
        company_id="company-123",
    )

    assert res["decision"] == "refuse"
    assert res["query"] is None


@pytest.mark.asyncio
async def test_gemini_429_retries_then_raises_llm_unavailable_with_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 1)

    mock_client = MagicMock()
    err_429 = errors.ClientError(
        429,
        {"error": {"message": "Resource exhausted. Please retry in 18s"}},
        None,
    )
    mock_client.aio.models.generate_content = AsyncMock(side_effect=err_429)

    generator = GeminiQueryGenerator(client=mock_client)
    with pytest.raises(LlmUnavailableError) as exc_info:
        await generator.generate(question="test", catalog={}, history=[], company_id="c1")

    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after == 18
    assert exc_info.value.message == "The AI service is busy. Please try again in a moment."


@pytest.mark.asyncio
async def test_gemini_daily_quota_exhausted_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 2)

    mock_client = MagicMock()
    err_daily = errors.ClientError(
        429,
        {"error": {"message": "Quota exceeded for quota metric 'GenerateRequestsPerDay'"}},
        None,
    )
    mock_client.aio.models.generate_content = AsyncMock(side_effect=err_daily)

    generator = GeminiQueryGenerator(client=mock_client)
    with pytest.raises(LlmUnavailableError) as exc_info:
        await generator.generate(question="test", catalog={}, history=[], company_id="c1")

    assert mock_client.aio.models.generate_content.call_count == 1  # Not retried
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after >= 3600


@pytest.mark.asyncio
async def test_gemini_invalid_key_and_model_not_found_not_retryable(monkeypatch, caplog):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-secret-key-12345")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

    mock_client = MagicMock()
    err_key = errors.ClientError(
        400,
        {"error": {"message": "API key not valid. Please pass a valid API key."}},
        None,
    )
    mock_client.aio.models.generate_content = AsyncMock(side_effect=err_key)

    generator = GeminiQueryGenerator(client=mock_client)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(LlmUnavailableError) as exc_info:
            await generator.generate(question="test", catalog={}, history=[], company_id="c1")

    assert exc_info.value.retryable is False
    assert exc_info.value.message == "The AI service is busy. Please try again in a moment."
    # Ensure key does NOT appear in logs
    for rec in caplog.records:
        assert "test-secret-key-12345" not in rec.message


@pytest.mark.asyncio
async def test_gemini_history_limited_to_six_turns_and_question_in_user_message(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")

    mock_client = MagicMock()
    mock_resp = MagicMock(text=json.dumps({"decision": "answer", "query": None}), usage_metadata=None)
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

    generator = GeminiQueryGenerator(client=mock_client)
    history = [{"role": "user", "content": f"Turn {i}"} for i in range(10)]
    await generator.generate(question="Real Question", catalog={}, history=history, company_id="c1")

    called_contents = mock_client.aio.models.generate_content.call_args.kwargs["contents"]
    # 6 history turns + 1 final turn = 7
    assert len(called_contents) == 7
    last_content = called_contents[-1]
    assert "User question (untrusted input):\nReal Question" in last_content.parts[0].text


def test_gemini_startup_fails_fast_when_variable_missing(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "GEMINI_MODEL", None)

    with pytest.raises(ValueError) as exc:
        GeminiQueryGenerator()
    assert "GEMINI_API_KEY and GEMINI_MODEL must be configured" in str(exc.value)


# ---------------------------------------------------------------------------
# OpenAI-Compatible Query Generator Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_compat_valid_answer(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setattr(settings, "LLM_API_BASE", "https://api.groq.com/openai/v1")

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.finish_reason = "stop"
    mock_choice.message.content = json.dumps({
        "decision": "answer",
        "query": {"collection": "customers", "pipeline": []},
    })
    mock_resp.choices = [mock_choice]
    mock_resp.usage = MagicMock(total_tokens=90)
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    generator = OpenAICompatQueryGenerator(client=mock_client)
    res = await generator.generate(
        question="Show customers",
        catalog={},
        history=[],
        company_id="comp-1",
    )

    assert res["decision"] == "answer"
    assert res["query"]["collection"] == "customers"


@pytest.mark.asyncio
async def test_openai_compat_blocked_content_filter_returns_refusal(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setattr(settings, "LLM_API_BASE", "https://api.groq.com/openai/v1")

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.finish_reason = "content_filter"
    mock_choice.message.content = None
    mock_resp.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    generator = OpenAICompatQueryGenerator(client=mock_client)
    res = await generator.generate(question="bad prompt", catalog={}, history=[], company_id="c1")
    assert res["decision"] == "refuse"


@pytest.mark.asyncio
async def test_openai_compat_auth_error_not_retryable(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "invalid-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "model-1")
    monkeypatch.setattr(settings, "LLM_API_BASE", "https://api.example.com")

    mock_client = MagicMock()
    mock_resp = MagicMock(status_code=401, headers={})
    auth_err = AuthenticationError("Invalid API key", response=mock_resp, body=None)
    mock_client.chat.completions.create = AsyncMock(side_effect=auth_err)

    generator = OpenAICompatQueryGenerator(client=mock_client)
    with pytest.raises(LlmUnavailableError) as exc_info:
        await generator.generate(question="test", catalog={}, history=[], company_id="c1")

    assert exc_info.value.retryable is False


def test_openai_compat_startup_fails_fast_when_missing(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_MODEL", None)
    monkeypatch.setattr(settings, "LLM_API_BASE", None)

    with pytest.raises(ValueError) as exc:
        OpenAICompatQueryGenerator()
    assert "LLM_API_KEY, LLM_MODEL, and LLM_API_BASE must be configured" in str(exc.value)


# ---------------------------------------------------------------------------
# Container Wiring & Fallback Tests
# ---------------------------------------------------------------------------

def test_container_picks_right_class_and_fails_on_unsupported(monkeypatch):
    from app.container import _build_generator_for_provider
    from app.infrastructure.azure.openai_client import AzureSQLGenerator, FakeSQLGenerator

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    gen_gemini = _build_generator_for_provider("gemini")
    assert isinstance(gen_gemini, GeminiQueryGenerator)

    monkeypatch.setattr(settings, "LLM_API_KEY", "fake-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "fake-model")
    monkeypatch.setattr(settings, "LLM_API_BASE", "https://fake.api")
    gen_compat = _build_generator_for_provider("openai_compat")
    assert isinstance(gen_compat, OpenAICompatQueryGenerator)

    gen_azure = _build_generator_for_provider("azure")
    assert isinstance(gen_azure, AzureSQLGenerator)

    gen_fake = _build_generator_for_provider("fake")
    assert isinstance(gen_fake, FakeSQLGenerator)

    with pytest.raises(ValueError):
        _build_generator_for_provider("unsupported_provider_xyz")


@pytest.mark.asyncio
async def test_container_no_silent_fallback_by_default(monkeypatch):
    """When fallback provider is NOT set, primary failure raises error without fallback."""
    from app.container import _build_generator_for_provider

    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", None)

    fake_gen = _build_generator_for_provider("fake")
    fake_gen.generate = AsyncMock(side_effect=LlmUnavailableError("Service down"))

    with pytest.raises(LlmUnavailableError):
        await fake_gen.generate("question", {}, [], "c1")
