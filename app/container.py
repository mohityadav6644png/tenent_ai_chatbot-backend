"""
Container (app/container.py)
Composition root: Wires interfaces to concrete implementations.
"""

from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.logging import logger
from app.infrastructure.azure.openai_client import AzureSQLGenerator, FakeSQLGenerator, SQLGenerator
from app.infrastructure.azure.redis_cache import get_cache_client
from app.infrastructure.company.csv_parser import StandardCsvParser
from app.infrastructure.db.chat_repo import MongoChatRepository
from app.infrastructure.db.company_repo import (
    MongoCompanyRecordsRepository,
    MongoDataImportRepository,
    MongoDataIngestionRepository,
)
from app.infrastructure.db.executor import MongoExecutor, PostgresExecutor
from app.infrastructure.db.reports_repo import MongoReportsRepository
from app.infrastructure.db.repositories import (
    MongoAuditRepository,
    MongoAuthRepository,
    MongoTenantRepository,
    MongoUserRepository,
    SchemaCatalogRepo,
)
from app.modules.audit.service import AuditService
from app.modules.auth.service import AuthService
from app.modules.chat.service import QueryService
from app.modules.company.service import CompanyService
from app.modules.reports.service import ReportService
from app.modules.tenants.service import TenantService
from app.modules.users.service import UserService


def _build_generator_for_provider(provider_name: str) -> SQLGenerator:
    p = (provider_name or "").strip().lower()
    if p == "gemini":
        from app.infrastructure.gemini.client import GeminiQueryGenerator

        return GeminiQueryGenerator()
    elif p == "openai_compat":
        from app.infrastructure.llm.openai_compat_client import OpenAICompatQueryGenerator

        return OpenAICompatQueryGenerator()
    elif p == "azure":
        return AzureSQLGenerator()
    elif p == "fake":
        return FakeSQLGenerator()
    else:
        raise ValueError(
            f"Unsupported LLM_PROVIDER: '{provider_name}'. Must be one of: 'gemini', 'openai_compat', 'azure', 'fake'"
        )


class FallbackSQLGenerator:
    """Delegates to primary generator; uses fallback only when explicitly configured on non-retryable failure."""

    def __init__(self, primary: SQLGenerator, fallback: SQLGenerator, primary_name: str, fallback_name: str):
        self.primary = primary
        self.fallback = fallback
        self.primary_name = primary_name
        self.fallback_name = fallback_name

    async def generate(
        self, question: str, catalog: Dict[str, Any], history: List[Dict[str, str]], company_id: str
    ) -> Dict[str, Any]:
        try:
            logger.info(f"Using primary LLM provider: {self.primary_name}")
            return await self.primary.generate(question, catalog, history, company_id)
        except Exception as exc:
            logger.warning(
                f"Primary provider '{self.primary_name}' failed: {type(exc).__name__}. Falling back to '{self.fallback_name}'"
            )
            return await self.fallback.generate(question, catalog, history, company_id)


class Container:
    def __init__(self):
        # Repositories & Infrastructures
        self.auth_repo = MongoAuthRepository()
        self.audit_repo = MongoAuditRepository()
        self.tenant_repo = MongoTenantRepository()
        self.user_repo = MongoUserRepository()
        self.chat_repo = MongoChatRepository()
        self.reports_repo = MongoReportsRepository()
        self.catalog_repo = SchemaCatalogRepo()
        self.cache = get_cache_client()
        self.company_records_repo = MongoCompanyRecordsRepository()
        self.data_ingestion_repo = MongoDataIngestionRepository()
        self.data_import_repo = MongoDataImportRepository()
        self.csv_parser = StandardCsvParser()

        from app.infrastructure.db.record_search_repo import MongoRecordSearchRepository
        from app.infrastructure.llm.answer_writer import LlmAnswerWriter

        self.record_search_repo = MongoRecordSearchRepository()
        self.answer_writer = LlmAnswerWriter()

        # Database and AI engines (MongoExecutor for MongoDB business collections)
        self.executor = MongoExecutor(
            max_rows=settings.MAX_ROWS, timeout_seconds=settings.QUERY_TIMEOUT_SECONDS
        )
        provider = settings.LLM_PROVIDER.lower().strip()
        if settings.QUERY_PLAN_ENABLED:
            from app.infrastructure.llm.plan_generator import QueryPlanGenerator
            primary_gen = QueryPlanGenerator(provider)
        else:
            primary_gen = _build_generator_for_provider(provider)

        fallback_provider = (settings.LLM_FALLBACK_PROVIDER or "").lower().strip()
        if fallback_provider:
            fallback_gen = _build_generator_for_provider(fallback_provider)
            self.generator = FallbackSQLGenerator(primary_gen, fallback_gen, provider, fallback_provider)
        else:
            self.generator = primary_gen

        # Core Domain Services
        self.audit_service = AuditService(repo=self.audit_repo)
        self.auth_service = AuthService(repo=self.auth_repo)
        self.tenant_service = TenantService(repo=self.tenant_repo, audit=self.audit_service)
        self.user_service = UserService(repo=self.user_repo)
        self.query_service = QueryService(
            executor=self.executor,
            generator=self.generator,
            repo=self.chat_repo,
            cache=self.cache,
            record_search_repo=self.record_search_repo,
            answer_writer=self.answer_writer,
        )
        self.report_service = ReportService(
            repo=self.reports_repo, query_service=self.query_service
        )
        self.company_service = CompanyService(
            records_repo=self.company_records_repo,
            ingestion_repo=self.data_ingestion_repo,
            tracking_repo=self.data_import_repo,
            csv_parser=self.csv_parser,
            tenant_repo=self.tenant_repo,
            cache=self.cache,
            audit_service=self.audit_service,
        )


container = Container()


def get_auth_service() -> AuthService:
    return container.auth_service


def get_auth_repo() -> MongoAuthRepository:
    return container.auth_repo


def get_audit_service() -> AuditService:
    return container.audit_service


def get_tenant_service() -> TenantService:
    return container.tenant_service


def get_user_service() -> UserService:
    return container.user_service


def get_query_service() -> QueryService:
    return container.query_service


def get_report_service() -> ReportService:
    return container.report_service


def get_company_service() -> CompanyService:
    return container.company_service
