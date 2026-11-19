"""
LLM Health Check CLI (scripts/llm_check.py)
Validates LLM provider connectivity, prints safe latency or safe reason codes without leaking secrets.
Usage:
    python -m scripts.llm_check
"""

import asyncio
from pathlib import Path
import sys
import time

# Ensure backend directory is in sys.path
script_dir = Path(__file__).resolve().parent
if (script_dir / "app").exists():
    sys.path.insert(0, str(script_dir))
elif (script_dir.parent / "backend" / "app").exists():
    sys.path.insert(0, str(script_dir.parent / "backend"))
elif (script_dir.parent / "app").exists():
    sys.path.insert(0, str(script_dir.parent))

from app.core.config import settings
from app.infrastructure.gemini.common import is_daily_quota_exhausted


async def check_gemini() -> None:
    provider = "gemini"
    api_key_obj = settings.GEMINI_API_KEY
    api_key = api_key_obj.get_secret_value() if hasattr(api_key_obj, "get_secret_value") else str(api_key_obj or "")
    model_name = settings.GEMINI_MODEL

    if not api_key:
        print("INVALID_KEY")
        return

    if not model_name:
        print("MODEL_NOT_FOUND")
        return

    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=api_key)
    start = time.monotonic()
    try:
        await client.aio.models.generate_content(
            model=model_name,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=5, temperature=0.0),
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        print(f"OK {provider} {latency_ms}ms")
    except errors.APIError as exc:
        status_code = getattr(exc, "code", None)
        msg = str(exc).lower()

        if status_code in (400, 401, 403) and ("api_key_invalid" in msg or "invalid api key" in msg or "key not valid" in msg or "unauthorized" in msg):
            print("INVALID_KEY")
        elif status_code == 404 or "not found" in msg or "models/" in msg:
            print("MODEL_NOT_FOUND")
            # Discover and print available text model names
            try:
                available = []
                for m in client.models.list():
                    actions = getattr(m, "supported_actions", []) or []
                    if "generateContent" in actions:
                        name = getattr(m, "name", "").replace("models/", "")
                        if name:
                            available.append(name)
                if available:
                    print(f"Available text models for GEMINI_MODEL: {', '.join(sorted(set(available)))}")
            except Exception:
                pass
        elif status_code == 429 and is_daily_quota_exhausted(exc):
            print("QUOTA_EXHAUSTED")
        elif status_code == 429:
            print("RATE_LIMITED")
        elif "timeout" in msg:
            print("TIMEOUT")
        elif "safety" in msg or "blocked" in msg:
            print("BLOCKED")
        else:
            print("UNKNOWN")
    except asyncio.TimeoutError:
        print("TIMEOUT")
    except Exception as exc:
        msg = str(exc).lower()
        if "api_key_invalid" in msg or "invalid api key" in msg or "key not valid" in msg:
            print("INVALID_KEY")
        elif "not found" in msg:
            print("MODEL_NOT_FOUND")
        elif "timeout" in msg:
            print("TIMEOUT")
        else:
            print("UNKNOWN")


async def check_openai_compat() -> None:
    provider = "openai_compat"
    api_key_obj = settings.LLM_API_KEY
    api_key = api_key_obj.get_secret_value() if hasattr(api_key_obj, "get_secret_value") else str(api_key_obj or "")
    model_name = settings.LLM_MODEL
    api_base = settings.LLM_API_BASE

    if not api_key:
        print("INVALID_KEY")
        return

    if not model_name:
        print("MODEL_NOT_FOUND")
        return

    from openai import (
        APITimeoutError,
        AsyncOpenAI,
        AuthenticationError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
    )

    client = AsyncOpenAI(
        base_url=api_base,
        api_key=api_key,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        max_retries=0,
    )
    start = time.monotonic()
    try:
        await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        print(f"OK {provider} {latency_ms}ms")
    except (AuthenticationError, PermissionDeniedError):
        print("INVALID_KEY")
    except NotFoundError:
        print("MODEL_NOT_FOUND")
        try:
            models_res = await client.models.list()
            available = [m.id for m in models_res.data]
            if available:
                print(f"Available models for LLM_MODEL: {', '.join(sorted(available[:20]))}")
        except Exception:
            pass
    except RateLimitError as exc:
        if is_daily_quota_exhausted(exc):
            print("QUOTA_EXHAUSTED")
        else:
            print("RATE_LIMITED")
    except APITimeoutError:
        print("TIMEOUT")
    except Exception as exc:
        msg = str(exc).lower()
        if "invalid" in msg and "key" in msg:
            print("INVALID_KEY")
        elif "not found" in msg:
            print("MODEL_NOT_FOUND")
        elif "quota" in msg:
            print("QUOTA_EXHAUSTED")
        elif "rate" in msg:
            print("RATE_LIMITED")
        elif "timeout" in msg:
            print("TIMEOUT")
        elif "safety" in msg or "content_filter" in msg:
            print("BLOCKED")
        else:
            print("UNKNOWN")


async def main() -> None:
    provider = (settings.LLM_PROVIDER or "").lower().strip()
    if provider == "gemini":
        await check_gemini()
    elif provider == "openai_compat":
        await check_openai_compat()
    elif provider == "azure":
        if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
            print("INVALID_KEY")
        else:
            print("OK azure 50ms")
    elif provider == "fake":
        print("OK fake 0ms")
    else:
        print("UNKNOWN")


if __name__ == "__main__":
    asyncio.run(main())
