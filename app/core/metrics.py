"""
Stage Timers and Token Metrics Module (app/core/metrics.py)
Request-scoped latency stage tracking, LLM token capture, and structured metrics emission.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import time
from typing import Any, Dict, Optional
from app.core.config import settings
from app.core.logging import logger

_current_metrics: ContextVar[Optional["RequestMetrics"]] = ContextVar("current_metrics", default=None)


class RequestMetrics:
    """Collects per-stage timings, LLM token metrics, and catalog sizes for a single request."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.start_perf = time.perf_counter()
        self.stage_timings_ms: Dict[str, float] = {}
        self.token_usage: Dict[str, Dict[str, Any]] = {}
        self.catalog_files: int = 0
        self.catalog_chars: int = 0
        self.cache_hit: bool = False
        self.status: str = "success"
        self._emitted: bool = False

    @contextmanager
    def time_stage(self, stage_name: str):
        """Context manager to record stage execution duration in milliseconds."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            # Accumulate if stage is entered multiple times
            self.stage_timings_ms[stage_name] = round(
                self.stage_timings_ms.get(stage_name, 0.0) + elapsed_ms, 2
            )

    def record_tokens(
        self,
        purpose: str,
        model: str,
        prompt_tokens: int,
        output_tokens: int,
        total_tokens: int,
        thinking_tokens: int = 0,
        cached_tokens: int = 0,
        retries: int = 0,
    ) -> None:
        """Record token consumption for an LLM call purpose."""
        if purpose not in self.token_usage:
            self.token_usage[purpose] = {
                "model": model,
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "thinking_tokens": thinking_tokens,
                "cached_tokens": cached_tokens,
                "total_tokens": total_tokens,
                "call_count": 1,
                "retries": retries,
            }
        else:
            rec = self.token_usage[purpose]
            rec["prompt_tokens"] += prompt_tokens
            rec["output_tokens"] += output_tokens
            rec["thinking_tokens"] += thinking_tokens
            rec["cached_tokens"] += cached_tokens
            rec["total_tokens"] += total_tokens
            rec["call_count"] += 1
            rec["retries"] += retries

    def set_catalog_stats(self, num_files: int, total_chars: int) -> None:
        self.catalog_files = num_files
        self.catalog_chars = total_chars

    def finish(self, status: str = "success", cache_status: str = "miss") -> None:
        """Calculates total duration and emits one structured log line if CHAT_METRICS_LOG=true."""
        if self._emitted:
            return
        self._emitted = True
        self.status = status
        self.cache_hit = (cache_status == "hit")
        total_ms = (time.perf_counter() - self.start_perf) * 1000.0
        self.stage_timings_ms["total"] = round(total_ms, 2)

        if getattr(settings, "CHAT_METRICS_LOG", True):
            metrics_payload = {
                "event": "chat_metrics",
                "request_id": self.request_id,
                "status": self.status,
                "cache": "hit" if self.cache_hit else "miss",
                "timings_ms": self.stage_timings_ms,
                "tokens": self.token_usage,
                "catalog_files": self.catalog_files,
                "catalog_chars": self.catalog_chars,
            }
            # Pure structured metrics payload with NO user text, prompts, answers, or cell values
            logger.info(json.dumps(metrics_payload))

    def to_debug_dict(self) -> Dict[str, Any]:
        """Returns debug metadata for inclusion in response meta when CHAT_DEBUG_METRICS=true."""
        return {
            "timings_ms": self.stage_timings_ms,
            "tokens": self.token_usage,
            "catalog_chars": self.catalog_chars,
        }


def get_current_metrics() -> Optional[RequestMetrics]:
    """Retrieve current request metrics context."""
    return _current_metrics.get()


def init_request_metrics(request_id: str) -> RequestMetrics:
    """Initialize a new request metrics context."""
    metrics = RequestMetrics(request_id=request_id)
    _current_metrics.set(metrics)
    return metrics


def clear_request_metrics() -> None:
    """Clear request metrics context."""
    _current_metrics.set(None)


@contextmanager
def measure_stage(stage_name: str):
    """Convenience context manager for measuring a pipeline stage."""
    m = _current_metrics.get()
    if m:
        with m.time_stage(stage_name):
            yield
    else:
        yield


def extract_and_record_tokens(
    response: Any,
    purpose: str,
    model: str,
    retries: int = 0,
) -> None:
    """Extract token counts from GenAI/OpenAI response usage_metadata and record to current metrics."""
    m = get_current_metrics()
    if not m:
        return
    prompt_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cached_tokens = 0
    thinking_tokens = 0

    if hasattr(response, "usage_metadata") and response.usage_metadata:
        um = response.usage_metadata
        prompt_tokens = getattr(um, "prompt_token_count", 0) or 0
        output_tokens = getattr(um, "candidates_token_count", 0) or 0
        total_tokens = getattr(um, "total_token_count", 0) or (prompt_tokens + output_tokens)
        cached_tokens = getattr(um, "cached_content_token_count", 0) or 0
        thinking_tokens = getattr(um, "thinking_token_count", 0) or getattr(um, "reasoning_token_count", 0) or 0
    elif hasattr(response, "usage") and response.usage:
        u = response.usage
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        output_tokens = getattr(u, "completion_tokens", 0) or 0
        total_tokens = getattr(u, "total_tokens", 0) or (prompt_tokens + output_tokens)

    m.record_tokens(
        purpose=purpose,
        model=model,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        thinking_tokens=thinking_tokens,
        cached_tokens=cached_tokens,
        retries=retries,
    )
