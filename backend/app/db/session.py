"""Control-plane database engine and session lifecycle.

This module owns the single async engine and session factory for the platform's *own*
PostgreSQL database. Target (user-registered) databases are reached exclusively through the
DB Adapter Layer (Phase 4) and never through this engine — the two data planes are isolated.

Usage
-----
* ``init_engine(settings)`` / ``dispose_engine()`` are driven by the app lifespan.
* ``get_session`` is the FastAPI dependency that yields a transaction-scoped session and
  commits on success / rolls back on error.
* ``ping()`` performs a lightweight liveness check for the health endpoint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import ControlPlaneDBSettings
from app.core.exceptions import ServiceUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Module-level singletons, owned by the app lifespan. Accessed only via the helpers below.
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: ControlPlaneDBSettings) -> AsyncEngine:
    """Create the global async engine and session factory. Idempotent."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine

    _engine = create_async_engine(
        settings.dsn(),
        echo=settings.echo_sql,
        pool_pre_ping=True,  # transparently recycle stale connections
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_recycle=settings.pool_recycle,
    )
    _sessionmaker = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    logger.info(
        "Control-plane engine initialized",
        extra={"db_host": settings.host, "db_name": settings.name},
    )
    return _engine


async def dispose_engine() -> None:
    """Dispose the global engine and reset factories. Driven by lifespan shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        logger.info("Control-plane engine disposed")
    _engine = None
    _sessionmaker = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise ServiceUnavailableError("Database engine is not initialized.")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise ServiceUnavailableError("Database session factory is not initialized.")
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session, commit on success, roll back on failure.

    The session is always closed. Application errors propagate unchanged so the API layer
    can map them; SQLAlchemy errors are wrapped to avoid leaking driver internals.
    """
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Database transaction failed; rolled back")
        raise
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context-manager variant of :func:`get_session` for use outside request handlers."""
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def ping() -> bool:
    """Return ``True`` if the control-plane database answers ``SELECT 1``."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        # A health check must never propagate — any failure (driver auth, DNS, timeout,
        # SQLAlchemy wrapping) means "not ready", reported by the readiness probe.
        logger.warning("Control-plane database ping failed", exc_info=True)
        return False
