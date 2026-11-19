"""
Database Engine Setup (app/infrastructure/db/engine.py)
SQLAlchemy 2 async engine and sessionmaker for PostgreSQL business data.
"""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings
from app.core.logging import logger

_engine: Optional[AsyncEngine] = None
_reader_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


async def init_postgres_engine() -> None:
    """Initialize Postgres owner and reader engines."""
    global _engine, _reader_engine, _session_factory
    try:
        if settings.ENVIRONMENT != "testing" and settings.POSTGRES_URL:
            _engine = create_async_engine(settings.POSTGRES_URL, pool_pre_ping=True)
            _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        if settings.ENVIRONMENT != "testing" and settings.POSTGRES_READER_URL:
            _reader_engine = create_async_engine(settings.POSTGRES_READER_URL, pool_pre_ping=True)
        logger.info("PostgreSQL engine initialized")
    except Exception as exc:
        logger.warning(f"PostgreSQL connection initialization warning: {exc}")


async def close_postgres_engine() -> None:
    """Dispose of engine connection pools."""
    global _engine, _reader_engine
    if _engine:
        await _engine.dispose()
    if _reader_engine:
        await _reader_engine.dispose()
    logger.info("PostgreSQL engines disposed")


def get_engine() -> Optional[AsyncEngine]:
    return _engine


def get_reader_engine() -> Optional[AsyncEngine]:
    return _reader_engine


def get_session_factory() -> Optional[async_sessionmaker[AsyncSession]]:
    return _session_factory
