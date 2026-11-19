"""
Gemini Query Generator (app/infrastructure/gemini/client.py)
Hardened free-tier client using official 'google-genai' SDK with rate limiting, retries, and safety guards.
"""

import asyncio
import json
import random
import time
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import errors, types
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

_gemini_client: Optional[genai.Client] = None
_gemini_limiter: Optional[InProcessLimiter] = None


def get_gemini_limiter() -> InProcessLimiter:
    global _gemini_limiter
    if _gemini_limiter is None:
        _gemini_limiter = InProcessLimiter()
    return _gemini_limiter


def get_gemini_client() -> genai.Client:
    """Return singleton async-capable google-genai client."""
    global _gemini_client
    if _gemini_client is None:
        api_key_obj = settings.GEMINI_API_KEY
        api_key = api_key_obj.get_secret_value() if hasattr(api_key_obj, "get_secret_value") else str(api_key_obj or "")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


class GeminiQueryGenerator:
    """Enterprise-hardened Gemini client implementing SQLGenerator interface."""

    def __init__(self, client: Optional[genai.Client] = None, limiter: Optional[InProcessLimiter] = None):
        api_key_obj = settings.GEMINI_API_KEY
        api_key = api_key_obj.get_secret_value() if hasattr(api_key_obj, "get_secret_value") else str(api_key_obj or "")
        model_name = settings.GEMINI_MODEL

        # Fail fast startup check
        if not api_key or not model_name:
            raise ValueError(
                "Missing required configuration: GEMINI_API_KEY and GEMINI_MODEL must be configured when LLM_PROVIDER=gemini"
            )

        self.model_name = model_name
        self.max_retries = max(0, settings.GEMINI_MAX_RETRIES)
        self.client = client or get_gemini_client()
        self.limiter = limiter or get_gemini_limiter()

    async def generate(
        self,
        question: str,
        catalog: Dict[str, Any],
        history: List[Dict[str, str]],
        company_id: str,
    ) -> Dict[str, Any]:
        """Generate structured MongoDB aggregation query spec from natural language."""
        system_instruction = build_system_instruction(catalog)

        # Build contents from last N turns (default 6) without results or company_id
        contents: List[types.Content] = []
        recent_history = history[-settings.HISTORY_TURNS :] if history else []
        for h in recent_history:
            role = "user" if h.get("role") == "user" else "model"
            content_text = h.get("content", "")
            if content_text:
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=content_text)],
                    )
                )

        # Untrusted user input
        user_prompt = f"User question (untrusted input):\n{question}"
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_prompt)],
            )
        )

        config = types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            system_instruction=system_instruction,
        )

        # Execute call through limiter with retries
        raw_text, usage_tokens = await self._call_with_retries(contents, config)

        if not raw_text or not raw_text.strip():
            return build_generic_refusal()

        # Size check and JSON parsing with 1 repair attempt
        parsed = await self._parse_with_repair(raw_text, system_instruction, question, contents, config)

        # Record token consumption if available
        if usage_tokens > 0:
            await record_tenant_token_usage(company_id, usage_tokens)

        # Ensure $project is the final stage if followed only by $limit
        query_spec = parsed.get("query")
        if isinstance(query_spec, dict) and isinstance(query_spec.get("pipeline"), list):
            pipe = query_spec["pipeline"]
            if len(pipe) >= 2 and isinstance(pipe[-1], dict) and "$limit" in pipe[-1] and isinstance(pipe[-2], dict) and "$project" in pipe[-2]:
                pipe[-1], pipe[-2] = pipe[-2], pipe[-1]

        return parsed

    async def _call_with_retries(
        self, contents: List[types.Content], config: types.GenerateContentConfig
    ) -> tuple[str, int]:
        """Executes API call through in-process limiter with rate-limit retry logic."""
        attempt = 0
        backoff_base = 2.0

        while True:
            try:
                async with self.limiter.limit():
                    response = await self.client.aio.models.generate_content(
                        model=self.model_name,
                        contents=contents,
                        config=config,
                    )

                # Check safety block or empty response
                text = ""
                try:
                    text = response.text or ""
                except Exception:
                    # Candidates may be empty if blocked by safety filters
                    return "", 0

                tokens = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0

                return text, tokens

            except errors.APIError as exc:
                safe_log_llm_error("gemini", exc)
                status_code = getattr(exc, "code", None) or 500

                # 1. Non-retryable errors
                if is_invalid_key_error(exc) or is_model_not_found_error(exc) or status_code in (400, 401, 403, 404):
                    err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                    err.retry_after = 30
                    err.retryable = False
                    raise err

                # 2. Daily quota exhausted -> Do NOT retry, return longer retry_after
                if status_code == 429 and is_daily_quota_exhausted(exc):
                    retry_after = extract_retry_after(exc, default=3600)
                    err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                    err.retry_after = max(3600, retry_after)
                    err.retryable = True
                    raise err

                # 3. Transient 429 rate limit -> retry up to max_retries
                if status_code == 429:
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

                # 4. Other 5xx errors -> retry once or fail
                if status_code >= 500:
                    attempt += 1
                    if attempt <= self.max_retries:
                        await asyncio.sleep(1.0 + random.uniform(0.1, 0.3))
                        continue

                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = 30
                err.retryable = True
                raise err

            except LlmUnavailableError:
                raise

            except Exception as exc:
                safe_log_llm_error("gemini", exc)
                err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                err.retry_after = 30
                err.retryable = True
                raise err

    async def _parse_with_repair(
        self,
        raw_text: str,
        system_instruction: str,
        question: str,
        original_contents: List[types.Content],
        config: types.GenerateContentConfig,
    ) -> Dict[str, Any]:
        """Validates payload and performs 1 repair attempt if JSON is invalid or oversized."""
        try:
            return clean_and_parse_json(raw_text, max_bytes=MAX_RESPONSE_BYTES)
        except Exception:
            # 1 Repair attempt
            try:
                repair_prompt = (
                    f"Your previous response was invalid JSON or exceeded 8KB limit. "
                    f"Generate a strictly valid JSON MongoDB query spec under 8KB for question: {question}"
                )
                repair_contents = list(original_contents)
                repair_contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=repair_prompt)],
                    )
                )
                async with self.limiter.limit():
                    repair_response = await self.client.aio.models.generate_content(
                        model=self.model_name,
                        contents=repair_contents,
                        config=config,
                    )
                repaired_text = getattr(repair_response, "text", "") or ""
                return clean_and_parse_json(repaired_text, max_bytes=MAX_RESPONSE_BYTES)
            except Exception:
                # Fallback to standard refusal if repair attempt also fails
                return build_generic_refusal()
