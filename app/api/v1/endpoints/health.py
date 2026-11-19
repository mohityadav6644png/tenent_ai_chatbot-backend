"""
Health Endpoints (app/api/v1/endpoints/health.py)
Kubernetes probes (/healthz, /readyz) and super-admin internal subsystem diagnostics for MongoDB.
"""

from datetime import datetime, timezone
import time
from typing import Any, Dict
from urllib.parse import urlparse
from fastapi import APIRouter, Depends
from app.api.deps import require_roles
from app.api.v1.schemas.health import (
    HealthComponent,
    InternalHealthResponse,
    LivenessResponse,
    ReadinessResponse,
)
from app.core.config import settings
from app.core.context import TenantContext
from app.infrastructure.azure.redis_cache import get_cache_client
from app.infrastructure.db.mongodb import check_db_health

router = APIRouter(tags=["Health"])

_health_cache: Dict[str, Any] = {"data": None, "expires_at": 0}
_openai_cache: Dict[str, Any] = {"status": "operational", "expires_at": 0}


@router.get("/healthz", response_model=LivenessResponse)
async def liveness():
    return {"status": "ok"}


@router.get("/readyz", response_model=ReadinessResponse)
async def readiness():
    checks = {}
    mongo_ok = await check_db_health()
    checks["mongo"] = "ok" if mongo_ok else "down"

    cache = get_cache_client()
    try:
        await cache.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "degraded"

    overall = "ok" if mongo_ok else "down"
    return {"status": overall, "checks": checks}


@router.get("/internal/health", response_model=InternalHealthResponse)
async def internal_health(
    ctx: TenantContext = Depends(require_roles("super_admin")),
):
    now_ts = time.time()
    if _health_cache["data"] and now_ts < _health_cache["expires_at"]:
        return _health_cache["data"]

    now = datetime.now(timezone.utc)
    components = []

    # 1. API Core
    components.append(HealthComponent(key="api", label="FastAPI Application Core", status="operational", checked_at=now))

    # 2. Database (Checks both Mongo App and Business Reader credentials)
    mongo_ok = await check_db_health()
    db_status = "operational" if mongo_ok else "down"
    components.append(HealthComponent(key="database", label="MongoDB App & Business Reader", status=db_status, checked_at=now))

    # 3. AI / OpenAI component (key stays "openai")
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider == "gemini":
        ai_label = "Gemini"
    elif provider == "openai_compat":
        host = urlparse(settings.LLM_API_BASE or "").netloc
        ai_label = host if host else "openai_compat"
    elif provider == "azure":
        host = urlparse(settings.AZURE_OPENAI_ENDPOINT or "").netloc
        ai_label = f"Azure OpenAI ({host})" if host else "Azure OpenAI"
    else:
        ai_label = "Mock AI (Offline)"

    if now_ts > _openai_cache["expires_at"]:
        if provider == "fake":
            ai_status = "operational"
        elif provider == "gemini":
            has_key = bool(settings.GEMINI_API_KEY)
            has_model = bool(settings.GEMINI_MODEL)
            ai_status = "operational" if (has_key and has_model) else "degraded"
        elif provider == "openai_compat":
            has_key = bool(settings.LLM_API_KEY)
            has_model = bool(settings.LLM_MODEL)
            has_base = bool(settings.LLM_API_BASE)
            ai_status = "operational" if (has_key and has_model and has_base) else "degraded"
        elif provider == "azure":
            ai_status = "operational" if settings.AZURE_OPENAI_ENDPOINT else "degraded"
        else:
            ai_status = "degraded"
        _openai_cache["status"] = ai_status
        _openai_cache["expires_at"] = now_ts + 60
    else:
        ai_status = _openai_cache["status"]
    components.append(HealthComponent(key="openai", label=ai_label, status=ai_status, checked_at=now))

    # 4. Cache
    cache_status = "operational" if settings.REDIS_URL else "degraded"
    components.append(HealthComponent(key="cache", label="Distributed & In-Memory Cache", status=cache_status, checked_at=now))

    resp = {"components": components}
    _health_cache["data"] = resp
    _health_cache["expires_at"] = now_ts + 15
    return resp


# Retain legacy /health
@router.get("/health")
async def health():
    db_connected = await check_db_health()
    return {"status": "healthy" if db_connected else "degraded", "database_connected": db_connected, "environment": settings.ENVIRONMENT}
