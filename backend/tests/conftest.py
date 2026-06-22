"""Shared test fixtures.

Auth/persistence is exercised against an in-memory async SQLite database (the User model is
deliberately dialect-agnostic). The control-plane ``get_session`` dependency is overridden to
use this engine, so the real API stack — middleware, dependencies, RBAC — is tested end to
end without a PostgreSQL instance.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.roles import Role
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models.user import User
from app.security import password as pwd

# Required secrets so Settings validates and tokens can be signed during tests.
_TEST_ENV = {
    "SECURITY_JWT_SECRET": "test-secret-test-secret-test-secret-123456",
    "SECURITY_MASTER_ENCRYPTION_KEY": base64.b64encode(b"\x00" * 32).decode(),
    "APP_ENVIRONMENT": "local",
}


@pytest.fixture(scope="session", autouse=True)
def _env() -> Iterator[None]:
    import os

    saved = {k: os.environ.get(k) for k in _TEST_ENV}
    os.environ.update(_TEST_ENV)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[object]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker(engine: object) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def seed_users(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, User]:
    users = {
        "admin": User(
            email="admin@test.com",
            hashed_password=pwd.hash_password("admin-password-123"),
            role=Role.ADMIN,
            is_active=True,
        ),
        "viewer": User(
            email="viewer@test.com",
            hashed_password=pwd.hash_password("viewer-password-123"),
            role=Role.VIEWER,
            is_active=True,
        ),
    }
    async with sessionmaker() as session:
        for user in users.values():
            session.add(user)
        await session.commit()
        for user in users.values():
            await session.refresh(user)
    return users


@pytest_asyncio.fixture
async def client(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    from app.core.config import get_settings
    from app.services.audit_service import DatabaseAuditSink
    from app.services.orchestrator import ConnectionOrchestrator
    from app.services.query_engine import QueryEngine
    from app.services.sql_guard import SqlGuard

    app = create_app()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session

    # ASGITransport does not run the app lifespan, so wire the orchestrator manually.
    settings = get_settings()
    orchestrator = ConnectionOrchestrator(settings.connections)
    await orchestrator.start()
    app.state.orchestrator = orchestrator
    # Durable audit sink writing to the same in-memory test database the API reads from.
    audit_sink = DatabaseAuditSink(sessionmaker)
    app.state.query_engine = QueryEngine(SqlGuard(), audit_sink, settings.query)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await orchestrator.stop()
    app.dependency_overrides.clear()


@pytest.fixture
def fake_pg() -> Iterator[None]:
    """Register the in-memory FakeAdapter for the PostgreSQL engine for one test."""
    from app.db.adapters import registry
    from app.db.engines import EngineType
    from tests.fakes import FakeAdapter

    registry.register_adapter(EngineType.POSTGRESQL, FakeAdapter)
    yield
    registry._registry.pop(EngineType.POSTGRESQL, None)  # noqa: SLF001


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
