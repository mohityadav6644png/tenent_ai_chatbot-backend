"""
API v1 Router (app/api/v1/router.py)
Aggregates all modular v1 endpoint routers.
"""

from fastapi import APIRouter
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.chat import router as chat_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.tenants import legacy_tenants_router, router as tenants_router
from app.api.v1.endpoints.reports import router as reports_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.audit import router as audit_router
from app.api.v1.endpoints.company import router as company_router

v1_router = APIRouter(prefix="/v1")

v1_router.include_router(auth_router, tags=["Auth"])
v1_router.include_router(chat_router, tags=["Chat"])
v1_router.include_router(users_router, tags=["Users"])
v1_router.include_router(tenants_router, tags=["Tenants"])
v1_router.include_router(legacy_tenants_router, tags=["Tenants"])
v1_router.include_router(reports_router, tags=["Reports"])
v1_router.include_router(health_router, tags=["Health"])
v1_router.include_router(audit_router, tags=["Audit"])
v1_router.include_router(company_router, prefix="/company", tags=["Company data"])
