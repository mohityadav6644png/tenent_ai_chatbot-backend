"""
OpenAI-Compatible Query Generator (app/infrastructure/llm/openai_compat_client.py)
Supports Groq, OpenRouter, Mistral, Together, and self-hosted vLLM/Ollama endpoints.
"""

import asyncio
import json
import random
from typing import Any, Dict, List, Optional
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.core.config import settings
from app.core.exceptions import LlmUnavailableError
from app.infrastructure.gemini.common import (
    MAX_RESPONSE_BYTES,
    build_generic_refusal,
    build_system_instruction,
    clean_and_parse_json,
    extract_retry_after,
    is_daily_quota_exhausted,
    is_invalid_key_error,
    is_model_not_found_error,
    record_tenant_token_usage,
    safe_log_llm_error,
)
from app.infrastructure.gemini.limiter import InProcessLimiter

_openai_compat_client: Optional[AsyncOpenAI] = None
_openai_compat_limiter: Optional[InProcessLimiter] = None


def get_openai_compat_limiter() -> InProcessLimiter:
    global _openai_compat_limiter
    if _openai_compat_limiter is None:
        _openai_compat_limiter = InProcessLimiter()
    return _openai_compat_limiter


def get_openai_compat_client() -> AsyncOpenAI:
    global _openai_compat_client
    if _openai_compat_client is None:
        api_key_obj = settings.LLM_API_KEY
        api_key = api_key_obj.get_secret_value() if hasattr(api_key_obj, "get_secret_value") else str(api_key_obj or "")
        _openai_compat_client = AsyncOpenAI(
            base_url=settings.LLM_API_BASE,
            api_key=api_key,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=0,  # Handled explicitly by our retry loop
        )
    return _openai_compat_client


class OpenAICompatQueryGenerator:
    """Async OpenAI-compatible generator for Groq, OpenRouter, and compatible APIs."""

    def __init__(self, client: Optional[AsyncOpenAI] = None, limiter: Optional[InProcessLimiter] = None):
        api_key_obj = settings.LLM_API_KEY
        api_key = api_key_obj.get_secret_value() if hasattr(api_key_obj, "get_secret_value") else str(api_key_obj or "")
        model_name = settings.LLM_MODEL
        api_base = settings.LLM_API_BASE

        if not api_key or not model_name or not api_base:
            raise ValueError(
                "Missing required configuration: LLM_API_KEY, LLM_MODEL, and LLM_API_BASE must be configured when LLM_PROVIDER=openai_compat"
            )

        self.model_name = model_name
        self.max_retries = max(0, settings.GEMINI_MAX_RETRIES)
        self.client = client or get_openai_compat_client()
        self.limiter = limiter or get_openai_compat_limiter()
        self._supports_json_mode = True

    async def generate(
        self,
        question: str,
        catalog: Dict[str, Any],
        history: List[Dict[str, str]],
        company_id: str,
    ) -> Dict[str, Any]:
        """Generate structured MongoDB aggregation query spec from natural language."""
        system_instruction = build_system_instruction(catalog)

        messages = [{"role": "system", "content": system_instruction}]

        # Add last N turns of history without company_id or query results
        recent_history = history[-settings.HISTORY_TURNS :] if history else []
        for h in recent_history:
            role = "user" if h.get("role") == "user" else "assistant"
            content = h.get("content", "")
            if content:
                messages.append({"role": role, "content": content})

        # Untrusted user input
        messages.append({"role": "user", "content": f"User question (untrusted input):\n{question}"})

        raw_text, usage_tokens = await self._call_with_retries(messages)

        if not raw_text or not raw_text.strip():
            return build_generic_refusal()

        parsed = await self._parse_with_repair(raw_text, messages, question)

        if usage_tokens > 0:
            await record_tenant_token_usage(company_id, usage_tokens)

        # Ensure $project is the final stage if followed only by $limit
        query_spec = parsed.get("query")
        if isinstance(query_spec, dict) and isinstance(query_spec.get("pipeline"), list):
            pipe = query_spec["pipeline"]
            if len(pipe) >= 2 and isinstance(pipe[-1], dict) and "$limit" in pipe[-1] and isinstance(pipe[-2], dict) and "$project" in pipe[-2]:
                pipe[-1], pipe[-2] = pipe[-2], pipe[-1]

        return parsed

    async def _call_with_retries(self, messages: List[Dict[str, str]]) -> tuple[str, int]:
        """Executes API call through in-process limiter with rate-limit retry logic."""
        attempt = 0
        backoff_base = 2.0

        while True:
            try:
                kwargs: Dict[str, Any] = {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": settings.LLM_TEMPERATURE,
                    "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
                }
                if self._supports_json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                async with self.limiter.limit():
                    response = await self.client.chat.completions.create(**kwargs)

                choice = response.choices[0] if response.choices else None
                if not choice or choice.finish_reason == "content_filter":
                    return "", 0

                raw_text = choice.message.content or ""
                tokens = 0
                if getattr(response, "usage", None):
                    tokens = getattr(response.usage, "total_tokens", 0) or 0

                return raw_text, tokens

            except BadRequestError as exc:
                # Handle providers where json_mode is not supported
                if self._supports_json_mode and ("response_format" in str(exc).lower() or "json_object" in str(exc).lower()):
                    self._supports_json_mode = False
                    continue

                safe_log_llm_error("openai_compat", exc)
                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = 30
                err.retryable = False
                raise err

            except (AuthenticationError, PermissionDeniedError, NotFoundError) as exc:
                safe_log_llm_error("openai_compat", exc)
                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = 30
                err.retryable = False
                raise err

            except RateLimitError as exc:
                safe_log_llm_error("openai_compat", exc)

                # Daily quota check
                if is_daily_quota_exhausted(exc):
                    retry_after = extract_retry_after(exc, default=3600)
                    err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                    err.retry_after = max(3600, retry_after)
                    err.retryable = True
                    raise err

                attempt += 1
                if attempt <= self.max_retries:
                    delay = backoff_base * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                    await asyncio.sleep(delay)
                    continue

                retry_after = extract_retry_after(exc, default=30)
                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = retry_after
                err.retryable = True
                raise err

            except (APITimeoutError, APIConnectionError, InternalServerError) as exc:
                safe_log_llm_error("openai_compat", exc)
                attempt += 1
                if attempt <= self.max_retries:
                    await asyncio.sleep(1.0 + random.uniform(0.1, 0.5))
                    continue

                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = 30
                err.retryable = True
                raise err

            except APIStatusError as exc:
                safe_log_llm_error("openai_compat", exc)
                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = 30
                err.retryable = exc.status_code >= 500
                raise err

            except LlmUnavailableError:
                raise

            except Exception as exc:
                safe_log_llm_error("openai_compat", exc)
                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = 30
                err.retryable = True
                raise err

    async def _parse_with_repair(
        self,
        raw_text: str,
        original_messages: List[Dict[str, str]],
        question: str,
    ) -> Dict[str, Any]:
        """Validates payload and performs 1 repair attempt if JSON is invalid or oversized."""
        try:
            return clean_and_parse_json(raw_text, max_bytes=MAX_RESPONSE_BYTES)
        except Exception:
            try:
                repair_messages = list(original_messages)
                repair_messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response was invalid JSON or exceeded 8KB limit. "
                            f"Generate a strictly valid JSON MongoDB query spec under 8KB for question: {question}"
                        ),
                    }
                )
                async with self.limiter.limit():
                    kwargs: Dict[str, Any] = {
                        "model": self.model_name,
                        "messages": repair_messages,
                        "temperature": settings.LLM_TEMPERATURE,
                        "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
                    }
                    if self._supports_json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    repair_response = await self.client.chat.completions.create(**kwargs)

                choice = repair_response.choices[0] if repair_response.choices else None
                repaired_text = choice.message.content or "" if choice else ""
                return clean_and_parse_json(repaired_text, max_bytes=MAX_RESPONSE_BYTES)
            except Exception:
                return build_generic_refusal()
