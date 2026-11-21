"""
Redis Cache Client (app/infrastructure/azure/redis_cache.py)
Distributed cache with unified key builder and transparent in-memory fallback.
"""

import hashlib
import time
from typing import Any, Dict, Optional
import redis.asyncio as aioredis
from app.core.config import settings
from app.core.logging import logger


def build_cache_key(company_id: str, schema_version: int, normalized_question: str) -> str:
    """Single unified cache key builder for SQL generation and query caching."""
    q_hash = hashlib.sha256(normalized_question.strip().lower().encode("utf-8")).hexdigest()
    return f"v1:{company_id}:{schema_version}:{q_hash}"


class InMemoryCache:
    """Thread-safe and async-friendly in-memory cache for fallback when REDIS_URL is empty."""

    def __init__(self):
        self._data: Dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if not item:
            return None
        val, expires_at = item
        if expires_at and time.time() > expires_at:
            self._data.pop(key, None)
            return None
        return val

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        expires_at = time.time() + ex if ex else float("inf")
        self._data[key] = (value, expires_at)
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._data.pop(key, None) else 0

    async def ping(self) -> bool:
        return True

    async def incr(self, key: str) -> int:
        item = self._data.get(key)
        now = time.time()
        val = 0
        exp = float("inf")
        if item:
            val_str, exp = item
            if exp and now > exp:
                val = 0
            else:
                try:
                    val = int(val_str)
                except ValueError:
                    val = 0
        val += 1
        self._data[key] = (str(val), exp)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        if key in self._data:
            val, _ = self._data[key]
            self._data[key] = (val, time.time() + seconds)
            return True
        return False

    async def clear_tenant(self, company_id: str) -> int:
        prefix = f"v1:{company_id}:"
        to_del = [k for k in list(self._data.keys()) if k.startswith(prefix)]
        for k in to_del:
            self._data.pop(k, None)
        return len(to_del)


_redis_client: Any = None
_in_memory_client = InMemoryCache()


async def clear_tenant_cache(company_id: str, client: Any = None) -> int:
    """Clear all query and plan cache keys for a given tenant across L1 and L2."""
    from app.core.cache import invalidate_company_cache
    total = await invalidate_company_cache(company_id)
    c = client or get_cache_client()
    if hasattr(c, "clear_tenant"):
        total += await c.clear_tenant(company_id)
    else:
        try:
            keys = await c.keys(f"v1:{company_id}:*")
            if keys:
                total += await c.delete(*keys)
        except Exception:
            pass
    return total


async def init_redis() -> None:
    global _redis_client
    if settings.REDIS_URL:
        try:
            _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await _redis_client.ping()
            logger.info("Connected to Redis server")
        except Exception as exc:
            logger.warning(f"Could not connect to Redis, using in-memory cache fallback: {exc}")
            _redis_client = None
    else:
        logger.info("REDIS_URL empty, using in-memory cache fallback")


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        try:
            await _redis_client.close()
        except Exception:
            pass
        _redis_client = None


def get_cache_client() -> Any:
    return _redis_client if _redis_client is not None else _in_memory_client
