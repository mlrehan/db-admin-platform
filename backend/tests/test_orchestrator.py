"""Phase 3 — Connection Orchestrator lifecycle & isolation tests (with FakeAdapter)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.core.config import ConnectionSettings
from app.core.exceptions import NotFoundError, SessionLimitError, UnsupportedEngineError
from app.db.adapters import registry
from app.db.adapters.base import ConnectionConfig
from app.db.engines import EngineType
from app.services.orchestrator import ConnectionOrchestrator
from tests.fakes import FakeAdapter


@pytest.fixture
def fake_engine() -> Iterator[None]:
    """Register FakeAdapter for the PostgreSQL engine for the duration of a test."""
    registry.register_adapter(EngineType.POSTGRESQL, FakeAdapter)
    yield
    registry._registry.pop(EngineType.POSTGRESQL, None)  # noqa: SLF001 - test cleanup


def _config(*, fail: bool = False) -> ConnectionConfig:
    return ConnectionConfig(
        engine=EngineType.POSTGRESQL,
        host="db.internal",
        port=5432,
        database="app",
        username="svc",
        password="pw",
        options={"fail_connect": True} if fail else {},
    )


async def _orchestrator(**overrides) -> ConnectionOrchestrator:
    settings = ConnectionSettings(**overrides)
    orch = ConnectionOrchestrator(settings)
    return orch


async def test_open_and_get_session(fake_engine: None) -> None:
    orch = await _orchestrator()
    user = uuid.uuid4()
    session = await orch.open_session(
        user_id=user, connection_id=uuid.uuid4(), config=_config()
    )
    assert session.adapter.is_connected
    fetched = await orch.get_session(session.id, user_id=user)
    assert fetched.id == session.id
    await orch.close_all()


async def test_session_isolation_between_users(fake_engine: None) -> None:
    orch = await _orchestrator()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    s = await orch.open_session(user_id=alice, connection_id=uuid.uuid4(), config=_config())

    # Bob cannot resolve Alice's session — appears as not found.
    with pytest.raises(NotFoundError):
        await orch.get_session(s.id, user_id=bob)
    with pytest.raises(NotFoundError):
        await orch.close_session(s.id, user_id=bob)

    # Each user only lists their own sessions.
    assert len(await orch.list_sessions(user_id=alice)) == 1
    assert await orch.list_sessions(user_id=bob) == []
    await orch.close_all()


async def test_distinct_adapter_per_session(fake_engine: None) -> None:
    orch = await _orchestrator()
    user = uuid.uuid4()
    s1 = await orch.open_session(user_id=user, connection_id=uuid.uuid4(), config=_config())
    s2 = await orch.open_session(user_id=user, connection_id=uuid.uuid4(), config=_config())
    assert s1.adapter is not s2.adapter  # no shared adapter/pool
    await orch.close_all()


async def test_session_limit_enforced(fake_engine: None) -> None:
    orch = await _orchestrator(max_sessions_per_user=2)
    user = uuid.uuid4()
    await orch.open_session(user_id=user, connection_id=uuid.uuid4(), config=_config())
    await orch.open_session(user_id=user, connection_id=uuid.uuid4(), config=_config())
    with pytest.raises(SessionLimitError):
        await orch.open_session(user_id=user, connection_id=uuid.uuid4(), config=_config())
    await orch.close_all()


async def test_close_session_closes_adapter(fake_engine: None) -> None:
    orch = await _orchestrator()
    user = uuid.uuid4()
    s = await orch.open_session(user_id=user, connection_id=uuid.uuid4(), config=_config())
    adapter = s.adapter
    await orch.close_session(s.id, user_id=user)
    assert not adapter.is_connected
    with pytest.raises(NotFoundError):
        await orch.get_session(s.id, user_id=user)


async def test_failed_connect_closes_adapter_and_raises(fake_engine: None) -> None:
    orch = await _orchestrator()
    with pytest.raises(RuntimeError):
        await orch.open_session(
            user_id=uuid.uuid4(), connection_id=uuid.uuid4(), config=_config(fail=True)
        )
    # No session should have been registered.
    assert await orch.list_sessions(user_id=uuid.uuid4()) == []


async def test_unsupported_engine_raises() -> None:
    orch = await _orchestrator()  # no adapter registered
    with pytest.raises(UnsupportedEngineError):
        await orch.open_session(
            user_id=uuid.uuid4(), connection_id=uuid.uuid4(), config=_config()
        )


async def test_idle_reaper_closes_stale_sessions(fake_engine: None) -> None:
    # ttl=0 → everything is immediately idle; tiny interval so the reaper fires fast.
    orch = await _orchestrator(session_idle_ttl_seconds=1, reaper_interval_seconds=1)
    user = uuid.uuid4()
    s = await orch.open_session(user_id=user, connection_id=uuid.uuid4(), config=_config())
    # Force staleness and reap directly (deterministic, no sleeping on the loop).
    await orch._reap_idle(ttl=-1)  # noqa: SLF001 - exercising internal reap logic
    assert not s.adapter.is_connected
    assert await orch.list_sessions(user_id=user) == []
    await orch.stop()
