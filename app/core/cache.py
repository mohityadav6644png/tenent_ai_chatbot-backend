"""
Two-Tier Tenant-Isolated Cache Module (app/core/cache.py)
Combines process-local in-memory TTL cache (L1 via cachetools.TTLCache)
and async Redis cache (L2 via redis.asyncio) with strict multi-tenant key prefixing:
  tenant:{company_id}:{namespace}:{key}
"""

import hashlib
import json
import time
from typing import Any, Dict, List, Optional
from cachetools import TTLCache
import redis.asyncio as aioredis
from app.core.config import settings
from app.core.logging import logger

# L1 Process-Local Cache: Max 10,000 items, default upper TTL 3,600s
# Entries store tuple of (value, expires_at_timestamp) for per-entry TTL precision.
_l1_cache: TTLCache = TTLCache(maxsize=10000, ttl=3600)

# L2 Redis Client (None if unconfigured or unreachable)
_redis_client: Optional[aioredis.Redis] = None


def build_tenant_cache_key(company_id: str, namespace: str, key: str) -> str:
    """Build strictly isolated tenant cache key."""
    c_id = str(company_id).strip()
    ns = str(namespace).strip()
    k = str(key).strip()
    return f"tenant:{c_id}:{ns}:{k}"


def hash_question(question: str) -> str:
    """Normalize and hash natural language question."""
    normalized = " ".join(question.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def init_redis(redis_url: Optional[str] = None) -> None:
    """
    Initialize L2 Redis client.
    Gracefully falls back to in-memory-only mode if Redis is unreachable or not configured.
    """
    global _redis_client
    target_url = redis_url if redis_url is not None else settings.REDIS_URL
    target_url = (target_url or "").strip()

    if not target_url:
        _redis_client = None
        logger.info("REDIS_URL not configured; running in in-memory-only cache mode (L1)")
        return

    try:
        client = aioredis.from_url(
            target_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        await client.ping()
        _redis_client = client
        logger.info("Connected to Redis cache (L2) at %s", target_url)
    except Exception as exc:
        logger.warning(
            "Could not connect to Redis at %s (%s). Falling back gracefully to in-memory L1 cache.",
            target_url,
            exc,
        )
        _redis_client = None


async def close_redis() -> None:
    """Close L2 Redis client connection cleanly."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.close()
        except Exception as exc:
            logger.warning("Error closing Redis connection: %s", exc)
        finally:
            _redis_client = None


def get_redis_client() -> Optional[aioredis.Redis]:
    """Return active Redis client or None."""
    return _redis_client


def get_l1_cache() -> TTLCache:
    """Return L1 in-memory cache instance."""
    return _l1_cache


async def cache_get(company_id: str, namespace: str, key: str) -> Optional[Any]:
    """
    Two-tier cache lookup:
    1. Check L1 in-memory TTL cache.
    2. Check L2 Redis cache. If hit, backfill L1 and return.
    3. Return None on miss.
    """
    if not company_id:
        return None

    full_key = build_tenant_cache_key(company_id, namespace, key)
    now = time.time()

    # 1. Check L1
    l1_entry = _l1_cache.get(full_key)
    if l1_entry is not None:
        val, expires_at = l1_entry
        if now < expires_at:
            return val
        # Expired in L1
        _l1_cache.pop(full_key, None)

    # 2. Check L2 Redis
    if _redis_client is not None:
        try:
            raw_val = await _redis_client.get(full_key)
            if raw_val is not None:
                try:
                    val = json.loads(raw_val)
                except Exception:
                    val = raw_val
                # Backfill L1 with 300s or remaining TTL
                _l1_cache[full_key] = (val, now + 300)
                return val
        except Exception as exc:
            logger.warning("L2 Redis cache_get error for key %s: %s", full_key, exc)

    return None


async def cache_set(
    company_id: str,
    namespace: str,
    key: str,
    value: Any,
    ttl_seconds: int = 300,
) -> bool:
    """
    Two-tier cache write:
    1. Store in L1 in-memory TTL cache.
    2. Store in L2 Redis cache with TTL.
    """
    if not company_id:
        return False

    full_key = build_tenant_cache_key(company_id, namespace, key)
    now = time.time()
    expires_at = now + ttl_seconds

    # 1. Store in L1
    _l1_cache[full_key] = (value, expires_at)

    # 2. Store in L2 Redis
    if _redis_client is not None:
        try:
            payload = json.dumps(value) if not isinstance(value, str) else value
            await _redis_client.set(full_key, payload, ex=ttl_seconds)
        except Exception as exc:
            logger.warning("L2 Redis cache_set error for key %s: %s", full_key, exc)

    return True


async def invalidate_company_cache(company_id: str) -> int:
    """
    Clear all cached keys for a specific company_id from both L1 memory and L2 Redis.
    Scans for prefix 'tenant:{company_id}:' so data never leaks across tenant boundaries.
    """
    if not company_id:
        return 0

    prefix = f"tenant:{str(company_id).strip()}:"
    invalidated_count = 0

    # 1. Invalidate L1 memory cache
    matching_l1_keys = [k for k in list(_l1_cache.keys()) if k.startswith(prefix)]
    for k in matching_l1_keys:
        _l1_cache.pop(k, None)
    invalidated_count += len(matching_l1_keys)

    # Also clear any legacy v1:{company_id}: keys in L1
    legacy_prefix = f"v1:{str(company_id).strip()}:"
    matching_legacy_l1 = [k for k in list(_l1_cache.keys()) if k.startswith(legacy_prefix)]
    for k in matching_legacy_l1:
        _l1_cache.pop(k, None)

    # 2. Invalidate L2 Redis cache via scan_iter
    if _redis_client is not None:
        try:
            redis_keys: List[str] = []
            async for k in _redis_client.scan_iter(match=f"{prefix}*"):
                redis_keys.append(k)
            async for k in _redis_client.scan_iter(match=f"{legacy_prefix}*"):
                redis_keys.append(k)

            if redis_keys:
                await _redis_client.delete(*redis_keys)
                invalidated_count += len(redis_keys)
        except Exception as exc:
            logger.warning("L2 Redis scan/delete error while invalidating tenant %s: %s", company_id, exc)

    logger.info("Invalidated %d cache keys for company %s", invalidated_count, company_id)
    return invalidated_count
