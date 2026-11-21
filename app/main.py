"""
Main Application Entry Point (app/main.py)
Initializes FastAPI, lifespan events, request tracking, security headers, and API routers.
"""

import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.api.error_handlers import register_error_handlers
from app.api.v1.endpoints.company import router as company_router
from app.api.v1.endpoints.chatbot import router as chatbot_router
from app.api.v1.router import v1_router
from app.core.config import settings
from app.core.logging import request_id_ctx
from app.core.cache import close_redis, init_redis
from app.infrastructure.db.mongodb import (
    client as mongo_client,
    close_mongodb_connection,
    connect_to_mongodb,
)

MAX_BODY_SIZE_BYTES = 1048576  # 1 MB


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENVIRONMENT != "testing":
        await connect_to_mongodb()
        await init_redis(settings.REDIS_URL)
    yield
    if settings.ENVIRONMENT != "testing":
        await close_mongodb_connection()
        await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description="Multi-tenant Enterprise Text-to-SQL Chatbot Backend",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.mongodb_client = mongo_client

    class RequestIdAndSecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # 1. Enforce payload size limit
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_BODY_SIZE_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"code": "PAYLOAD_TOO_LARGE", "message": "Request payload exceeds 1MB limit."},
                )

            # 2. Track Request-ID
            req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
            request.state.request_id = req_id
            token = request_id_ctx.set(req_id)
            try:
                response = await call_next(request)
                response.headers["X-Request-ID"] = req_id
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["X-XSS-Protection"] = "1; mode=block"
                if not any(request.url.path.startswith(p) for p in ["/docs", "/redoc", "/openapi.json"]):
                    response.headers["Content-Security-Policy"] = "default-src 'self'"
                if settings.COOKIE_SECURE:
                    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                return response
            finally:
                request_id_ctx.reset(token)

    app.add_middleware(RequestIdAndSecurityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(v1_router)
    app.include_router(company_router, prefix="/api/v1/company")
    app.include_router(chatbot_router, prefix="/api/v1/chatbot")

    @app.get("/")
    async def root():
        return {
            "app": settings.APP_NAME,
            "status": "online",
            "docs": "/docs",
            "api_v1": "/v1",
        }

    return app


app = create_app()
