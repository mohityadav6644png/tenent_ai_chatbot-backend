"""
Shared LLM Helpers (app/infrastructure/gemini/common.py)
Parsing, token accounting, error sanitization, and prompt utilities.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import yaml
from bson import ObjectId
from app.core.config import settings
from app.core.exceptions import LlmUnavailableError
from app.core.logging import logger

MAX_RESPONSE_BYTES = 8192  # 8 KB


def load_prompts() -> tuple[str, List[Dict[str, Any]]]:
    """Load canonical system prompt and few-shot examples from existing files without moving them."""
    base = Path(__file__).resolve().parent.parent / "azure" / "prompts"
    system_path = base / "system.md"
    few_shots_path = base / "few_shots.yaml"

    system_text = ""
    if system_path.exists():
        system_text = system_path.read_text(encoding="utf-8")

    few_shots = []
    if few_shots_path.exists():
        try:
            content = yaml.safe_load(few_shots_path.read_text(encoding="utf-8"))
            if isinstance(content, dict) and "examples" in content:
                few_shots = content["examples"]
        except Exception as exc:
            logger.warning(f"Could not parse few_shots.yaml: {exc}")

    return system_text, few_shots


def build_system_instruction(catalog: Dict[str, Any]) -> str:
    """Compose full system prompt including catalog schema and rules."""
    system_text, _ = load_prompts()
    catalog_json = json.dumps(catalog or {}, indent=2)
    return (
        f"{system_text}\n\n"
        f"## Tenant Schema Catalog (Active Collections and Fields):\n"
        f"```json\n{catalog_json}\n```\n\n"
        f"IMPORTANT: Respond ONLY with a valid JSON object matching Output JSON Schema. "
        f"No markdown fences, no explanatory text outside JSON. Max size is 8KB."
    )


def extract_retry_after(exc: Exception, default: int = 30) -> int:
    """Extract retry-after seconds from response headers or error messages."""
    # 1. Check response headers if available on exception
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None) or {}
        val = headers.get("retry-after") or headers.get("Retry-After")
        if val:
            try:
                return max(1, int(float(val)))
            except (ValueError, TypeError):
                pass

    # 2. Inspect error message for seconds/delay hints
    msg = str(exc)
    match = re.search(
        r"(?:retry\s+after|retry\s+in|reset\s+delay|wait)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(s|sec|seconds|m|min)?",
        msg,
        re.IGNORECASE,
    )
    if match:
        val = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        if unit.startswith("m"):
            val *= 60
        return max(1, int(val))

    return default


def is_daily_quota_exhausted(exc: Exception) -> bool:
    """Check if the error indicates a permanent daily or plan-level quota exhaustion."""
    msg = str(exc).lower()
    quota_indicators = [
        "daily",
        "per day",
        "per_day",
        "generaterequestsperday",
        "quota exceeded for quota metric",
        "free tier limit: 0",
        "day quota",
        "exceeded your current quota",
    ]
    return any(ind in msg for ind in quota_indicators)


def is_invalid_key_error(exc: Exception) -> bool:
    """Check if error is due to an invalid or revoked API key."""
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    msg = str(exc).lower()
    if status_code in (400, 401, 403):
        if any(term in msg for term in ["api_key_invalid", "invalid api key", "key not valid", "unauthorized", "api key expired"]):
            return True
    return "api_key_invalid" in msg or "invalid api key" in msg


def is_model_not_found_error(exc: Exception) -> bool:
    """Check if model does not exist or is deprecated."""
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    msg = str(exc).lower()
    return status_code == 404 or "not found" in msg or "model not found" in msg or "does not exist" in msg


def clean_and_parse_json(raw: str, max_bytes: int = MAX_RESPONSE_BYTES) -> Dict[str, Any]:
    """Validate payload size, clean markdown delimiters, and parse JSON safely."""
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) > max_bytes:
        raise ValueError(f"Response size ({len(raw_bytes)} bytes) exceeds limit ({max_bytes} bytes).")

    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Response root must be a JSON object.")

    if "decision" not in parsed:
        parsed["decision"] = "answer"

    if "query" in parsed and "sql" not in parsed:
        parsed["sql"] = json.dumps(parsed["query"]) if parsed["query"] else None

    # Guarantee canonical shape
    parsed.setdefault("clarification_question", None)
    parsed.setdefault("options", [])
    parsed.setdefault("headline_template", None)
    parsed.setdefault("detail_template", None)
    parsed.setdefault("follow_ups", [])
    return parsed


def build_generic_refusal() -> Dict[str, Any]:
    """Canonical refusal query spec."""
    return {
        "decision": "refuse",
        "query": None,
        "sql": None,
        "clarification_question": None,
        "options": [],
        "headline_template": None,
        "detail_template": None,
        "follow_ups": [],
    }


async def record_tenant_token_usage(company_id: Optional[str], tokens: int) -> None:
    """Record token consumption directly in tenant counters if company_id is provided."""
    if not company_id or tokens <= 0:
        return
    try:
        from app.infrastructure.db.mongodb import tenants_collection

        oid = ObjectId(company_id) if ObjectId.is_valid(company_id) else company_id
        await tenants_collection.update_one(
            {"_id": oid},
            {"$inc": {"ai_tokens_month": tokens}},
        )
    except Exception:
        pass


def safe_log_llm_error(provider: str, exc: Exception) -> None:
    """Logs ONLY error class, HTTP status, and provider name. Never leaks keys or prompts."""
    err_class = type(exc).__name__
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    status_str = f"HTTP {status_code}" if status_code else "no HTTP status"
    logger.error(f"[{provider}] {err_class} ({status_str})")
