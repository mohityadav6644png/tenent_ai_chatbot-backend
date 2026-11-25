"""
In-process Rate & Concurrency Limiter (app/infrastructure/gemini/limiter.py)
Prevents bursts of requests from exceeding free-tier limits without relying on Redis.
"""

import asyncio
from contextlib import asynccontextmanager
import time
from typing import List
from app.core.config import settings
from app.core.exceptions import LlmUnavailableError


class InProcessLimiter:
    """Limits concurrency and requests per minute in-process with a wait timeout."""

    def __init__(
        self,
        max_requests_per_minute: int | None = None,
        max_concurrency: int | None = None,
        wait_timeout: float = 20.0,
    ):
        self.max_rpm = max_requests_per_minute or settings.LLM_MAX_REQUESTS_PER_MINUTE
        self.max_concurrency = max_concurrency or settings.LLM_MAX_CONCURRENCY
        self.wait_timeout = wait_timeout
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._request_times: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        start = time.monotonic()

        # 1. Concurrency limit with timeout
        time_left = max(0.0, self.wait_timeout - (time.monotonic() - start))
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=time_left)
        except asyncio.TimeoutError:
            err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
            err.retry_after = 30
            err.retryable = True
            raise err

        # 2. Requests per minute limit with remaining timeout
        try:
            while True:
                now = time.monotonic()
                time_left = self.wait_timeout - (now - start)
                if time_left <= 0:
                    self._semaphore.release()
                    err = LlmUnavailableError("The AI service is busy. Please try again in a moment.")
                    err.retry_after = 30
                    err.retryable = True
                    raise err

                async with self._lock:
                    cutoff = now - 60.0
                    self._request_times = [t for t in self._request_times if t > cutoff]
                    if len(self._request_times) < self.max_rpm:
                        self._request_times.append(now)
                        return

                    oldest = self._request_times[0]
                    delay = min(oldest + 60.0 - now + 0.05, time_left)

                if delay > 0:
                    await asyncio.sleep(delay)
        except Exception:
            self._semaphore.release()
            raise

    def release(self) -> None:
        self._semaphore.release()

    @asynccontextmanager
    async def limit(self):
        await self.acquire()
        try:
            yield
        finally:
            self.release()
