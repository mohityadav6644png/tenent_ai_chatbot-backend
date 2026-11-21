"""
Core Config (app/core/config.py)
Loads configuration settings from environment variables and .env file.
"""

from functools import lru_cache
from pathlib import Path
from typing import List, Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "Company Chatbot Backend"
    APP_ENV: str = "development"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "dev-insecure-secret-key-32-chars-minimum-ok"

    # CORS & Cookies
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    COOKIE_SECURE: bool = False
    SESSION_TTL_MINUTES: int = 480
    CSRF_COOKIE_NAME: str = "csrf_token"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"

    # Databases (MongoDB App and Business Credentials)
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "app_data"
    MONGODB_DB: str = "app_data"
    MONGODB_BUSINESS_DB: str = "business_data"
    MONGODB_BUSINESS_URL_READER: str = "mongodb://localhost:27017"
    MONGODB_BUSINESS_URL_WRITER: str = "mongodb://localhost:27017"
    POSTGRES_URL: str = ""
    POSTGRES_READER_URL: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"

    # Legacy JWT Settings (retained for backward compatibility)
    JWT_SECRET_KEY: str = (
        "tCIz9OhLKultAsCmDnxVcficfX6PrJjzzwfZczVvClk_RWN5bSsnxzlAH8xN5Lsd"
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    VERIFY_EMAIL_EXPIRE_HOURS: int = 24
    RESET_PASSWORD_EXPIRE_HOURS: int = 1

    # AI / LLM Configuration
    LLM_PROVIDER: str = "fake"  # "gemini" | "openai_compat" | "azure" | "fake"
    LLM_FALLBACK_PROVIDER: Optional[str] = None
    LLM_MAX_REQUESTS_PER_MINUTE: int = 10
    LLM_MAX_CONCURRENCY: int = 3

    # Azure OpenAI Configuration
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o"
    AZURE_OPENAI_API_VERSION: str = "2024-02-15-preview"
    AZURE_OPENAI_API_KEY: Optional[str] = None
    AZURE_USE_MANAGED_IDENTITY: bool = False

    # Gemini Free Tier Configuration
    GEMINI_API_KEY: Optional[SecretStr] = None
    GEMINI_MODEL: Optional[str] = None
    GEMINI_MAX_RETRIES: int = 2

    # OpenAI-Compatible Configuration (Groq, OpenRouter, etc.)
    LLM_API_BASE: Optional[str] = None
    LLM_API_KEY: Optional[SecretStr] = None
    LLM_MODEL: Optional[str] = None
    LLM_TIMEOUT_SECONDS: float = 20.0
    LLM_MAX_OUTPUT_TOKENS: int = 1024
    LLM_TEMPERATURE: float = 0.0
    LLM_MODEL_PRIMARY_SMALL: Optional[str] = None
    LLM_ESCALATE_ON_FAILURE: bool = False

    # Email Service
    EMAIL_BACKEND: str = "console"  # "console" | "acs" | "smtp"
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@company.com"
    SMTP_FROM_NAME: str = "Company Auth"
    SMTP_TLS: bool = True
    ACS_CONNECTION_STRING: Optional[str] = None

    # URLs & Integrations
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    FRONTEND_URL: str = "http://localhost:5173"
    SERVICE_TOKEN: str = "internal-service-secret-token"

    # Rate Limiting & Query Controls
    RATE_LIMIT_USER_PER_MINUTE: int = 60
    RATE_LIMIT_TENANT_PER_MINUTE: int = 300
    MAX_ROWS: int = 100
    QUERY_TIMEOUT_SECONDS: int = 5
    HISTORY_TURNS: int = 6

    # Company Data Import & Ingestion Controls
    IMPORT_MAX_FILE_MB: int = 5
    IMPORT_MAX_ROWS: int = 50000
    IMPORT_MAX_COLUMNS: int = 30
    IMPORT_MAX_CELL_CHARS: int = 2000
    RECORD_TITLE_MAX: int = 200
    RECORD_CONTENT_MAX: int = 20000
    IMPORT_MAX_PER_HOUR: int = 10
    DATASET_STALE_DAYS: int = 30
    IMPORT_PREVIEW_ERRORS: int = 500
    FILES_MAX_PER_COMPANY: int = 10
    FILES_CATALOG_MAX_KB: int = 15

    # Structured Query Plan & Text Search
    QUERY_PLAN_ENABLED: bool = True
    QUERY_PLAN_MAX_LIMIT: int = 50
    TEXT_SEARCH_FALLBACK_ENABLED: bool = True
    TEXT_SEARCH_MAX_RESULTS: int = 3
    TEXT_SEARCH_SNIPPET_CHARS: int = 600
    ANSWER_WRITER_ENABLED: bool = False
    ANSWER_WRITER_MAX_ROWS: int = 10

    model_config = SettingsConfigDict(
        env_file=(str(BASE_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
