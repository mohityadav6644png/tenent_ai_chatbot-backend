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
from app.infrastructure.company_files.csv_parser import CompanyFilesCsvParser
from app.infrastructure.db.chat_repo import MongoChatRepository
from app.infrastructure.db.company_files_repo import MongoCompanyFilesRepository
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
from app.modules.company_files.service import CompanyFilesService
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


def _build_followup_resolver_for_provider(provider_name: str):
    p = (provider_name or "").strip().lower()
    if p == "gemini":
        from app.infrastructure.gemini.followup import GeminiFollowUpResolver
        return GeminiFollowUpResolver()
    elif p == "openai_compat":
        from app.infrastructure.llm.followup import OpenAICompatFollowUpResolver
        return OpenAICompatFollowUpResolver()
    elif p == "azure":
        from app.infrastructure.azure.followup import AzureFollowUpResolver
        return AzureFollowUpResolver()
    elif p == "fake":
        from app.infrastructure.azure.fake_followup import FakeFollowUpResolver
        return FakeFollowUpResolver()
    else:
        from app.infrastructure.azure.fake_followup import FakeFollowUpResolver
        return FakeFollowUpResolver()


def _build_profile_extractor_for_provider(provider_name: str):
    p = (provider_name or "").strip().lower()
    if p == "gemini":
        from app.infrastructure.profile_extractor.gemini_extractor import GeminiProfileExtractor
        return GeminiProfileExtractor()
    else:
        from app.infrastructure.profile_extractor.fake_extractor import FakeProfileExtractor
        return FakeProfileExtractor()


def _build_multidoc_planner_for_provider(provider_name: str):
    p = (provider_name or "").strip().lower()
    if p == "gemini":
        try:
            from app.infrastructure.multidoc_planner.gemini_planner import GeminiMultiDocPlanner
            return GeminiMultiDocPlanner()
        except Exception:
            from app.infrastructure.multidoc_planner.fake_planner import FakeMultiDocPlanner
            return FakeMultiDocPlanner()
    else:
        from app.infrastructure.multidoc_planner.fake_planner import FakeMultiDocPlanner
        return FakeMultiDocPlanner()


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

        # Multi-turn Follow-up Service
        from app.modules.chat.followup import FollowUpService
        resolver = _build_followup_resolver_for_provider(provider)
        self.followup_service = FollowUpService(resolver=resolver)

        # Progressive User Profiling
        from app.infrastructure.db.profile_repository import MongoProfileRepository
        from app.modules.profile.service import ProfileService
        self.profile_repo = MongoProfileRepository()
        self.profile_extractor = _build_profile_extractor_for_provider(provider)
        self.profile_service = ProfileService(
            repo=self.profile_repo,
            extractor=self.profile_extractor,
            user_repo=self.user_repo,
            tenant_repo=self.tenant_repo,
        )

        # Core Domain Services
        self.audit_service = AuditService(repo=self.audit_repo)
        self.auth_service = AuthService(repo=self.auth_repo)
        self.tenant_service = TenantService(repo=self.tenant_repo, audit=self.audit_service)
        self.user_service = UserService(repo=self.user_repo)
        from app.infrastructure.db.multidoc_repository import MongoMultiDocRepository
        from app.modules.multidoc.service import MultiDocService

        self.multidoc_repo = MongoMultiDocRepository()
        self.multidoc_planner = _build_multidoc_planner_for_provider(settings.LLM_PROVIDER)
        self.multidoc_service = MultiDocService(
            repo=self.multidoc_repo,
            planner=self.multidoc_planner,
            cache=self.cache,
        )
        self.query_service = QueryService(
            executor=self.executor,
            generator=self.generator,
            repo=self.chat_repo,
            cache=self.cache,
            record_search_repo=self.record_search_repo,
            answer_writer=self.answer_writer,
            followup_service=self.followup_service,
            profile_service=self.profile_service,
            multidoc_service=self.multidoc_service,
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
        self.company_files_repo = MongoCompanyFilesRepository()
        self.company_files_parser = CompanyFilesCsvParser()

        from app.infrastructure.db.file_connections_repo import MongoFileConnectionsRepository
        from app.infrastructure.file_connections.enrichment_worker import EnrichmentWorker
        from app.modules.file_connections.service import FileConnectionsService

        self.file_connections_repo = MongoFileConnectionsRepository()
        self.enrichment_worker = EnrichmentWorker(
            conn_repo=self.file_connections_repo,
            files_repo=self.company_files_repo,
            tenant_repo=self.tenant_repo,
            cache=self.cache,
            audit=self.audit_service,
        )
        self.file_connections_service = FileConnectionsService(
            conn_repo=self.file_connections_repo,
            files_repo=self.company_files_repo,
            enrichment_worker=self.enrichment_worker,
        )

        self.company_files_service = CompanyFilesService(
            repo=self.company_files_repo,
            parser=self.company_files_parser,
            tenant_repo=self.tenant_repo,
            cache=self.cache,
            audit=self.audit_service,
            file_connections_service=self.file_connections_service,
            enrichment_worker=self.enrichment_worker,
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


def get_company_files_service() -> CompanyFilesService:
    return container.company_files_service


def get_profile_service():
    return container.profile_service


def get_profile_repo():
    return container.profile_repo


def get_multidoc_service():
    return container.multidoc_service


def get_file_connections_service():
    return container.file_connections_service


def get_file_connections_repo():
    return container.file_connections_repo


def get_enrichment_worker():
    return container.enrichment_worker


