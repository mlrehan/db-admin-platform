"""Phase 5 — Query Engine tests (execute / stream / cancel / safety / audit)."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.core.config import QuerySettings
from app.core.exceptions import AuthorizationError, QueryCancelledError
from app.db.adapters.base import ConnectionConfig
from app.db.engines import EngineType
from app.services.audit_sink import QueryAuditEvent
from app.services.orchestrator import LiveSession
from app.services.query_engine import QueryEngine
from app.services.sql_guard import SqlGuard
from tests.fakes import FakeAdapter


class CapturingSink:
    def __init__(self) -> None:
        self.events: list[QueryAuditEvent] = []

    async def record_query(self, event: QueryAuditEvent) -> None:
        self.events.append(event)


def _user(role: str = "admin"):
    return SimpleNamespace(id=uuid.uuid4(), role=role)


async def _session(*, options: dict | None = None) -> LiveSession:
    config = ConnectionConfig(
        engine=EngineType.POSTGRESQL,
        host="h",
        port=5432,
        database="d",
        username="u",
        password="p",
        options=options or {},
    )
    adapter = FakeAdapter(config)
    await adapter.connect()
    return LiveSession(
        id=uuid.uuid4(), user_id=uuid.uuid4(), connection_id=uuid.uuid4(), adapter=adapter
    )


def _engine(sink: CapturingSink, **query_overrides) -> QueryEngine:
    return QueryEngine(SqlGuard(), sink, QuerySettings(**query_overrides))


async def test_execute_select_returns_rows() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session(options={"row_count": 3})
    result = await engine.execute(user=_user("admin"), session=session, sql="SELECT * FROM t")
    assert result.returns_rows is True
    assert result.row_count == 3
    assert [c.name for c in result.columns] == ["id", "label"]
    assert result.rows[0] == [0, "row-0"]
    assert len(sink.events) == 1 and sink.events[0].success is True


async def test_execute_insert_reports_affected() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session(options={"affected": 5})
    result = await engine.execute(user=_user("admin"), session=session, sql="INSERT INTO t VALUES (1)")
    assert result.returns_rows is False
    assert result.rows_affected == 5


async def test_permission_enforced_before_execution() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session()
    with pytest.raises(AuthorizationError):
        await engine.execute(user=_user("viewer"), session=session, sql="DROP TABLE t")
    # Enforcement happens before execution, so nothing is audited.
    assert sink.events == []


async def test_max_rows_truncation() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session(options={"row_count": 10})
    result = await engine.execute(
        user=_user("admin"), session=session, sql="SELECT * FROM t", max_rows=5
    )
    assert result.row_count == 5
    assert result.truncated is True


async def test_max_rows_capped_by_limit() -> None:
    sink = CapturingSink()
    engine = _engine(sink, max_rows_limit=2)
    session = await _session(options={"row_count": 10})
    result = await engine.execute(
        user=_user("admin"), session=session, sql="SELECT * FROM t", max_rows=1000
    )
    assert result.row_count == 2  # capped by max_rows_limit


async def test_cancellation() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session(options={"exec_delay": 5, "row_count": 1})
    user = _user("admin")

    task = asyncio.create_task(
        engine.execute(user=user, session=session, sql="SELECT * FROM t")
    )
    # Wait until the query registers as running, then cancel it by id.
    running = []
    for _ in range(50):
        running = await engine.list_running(user_id=user.id)
        if running:
            break
        await asyncio.sleep(0.02)
    assert running, "query did not register as running"
    await engine.cancel(running[0].id, user_id=user.id)

    with pytest.raises(QueryCancelledError):
        await task
    assert sink.events and sink.events[-1].error_code == "QUERY_CANCELLED"


async def test_cancel_unknown_query_raises() -> None:
    from app.core.exceptions import NotFoundError

    engine = _engine(CapturingSink())
    with pytest.raises(NotFoundError):
        await engine.cancel(uuid.uuid4(), user_id=uuid.uuid4())


async def test_stream_select_events() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session(options={"row_count": 4})
    events = [
        e
        async for e in engine.stream(
            user=_user("admin"), session=session, sql="SELECT * FROM t", batch_size=2
        )
    ]
    types = [e["type"] for e in events]
    assert types[0] == "accepted"
    assert "columns" in types
    assert types[-1] == "end"
    total = sum(len(e["rows"]) for e in events if e["type"] == "rows")
    assert total == 4
    assert events[-1]["row_count"] == 4
    assert sink.events and sink.events[-1].success is True


async def test_stream_write_reports_affected() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session(options={"affected": 7})
    events = [
        e
        async for e in engine.stream(
            user=_user("admin"), session=session, sql="UPDATE t SET x=1"
        )
    ]
    end = events[-1]
    assert end["type"] == "end"
    assert end["rows_affected"] == 7


async def test_stream_enforces_permission() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session()
    with pytest.raises(AuthorizationError):
        async for _ in engine.stream(
            user=_user("viewer"), session=session, sql="DROP TABLE t"
        ):
            pass


async def test_session_lock_released_after_stream() -> None:
    sink = CapturingSink()
    engine = _engine(sink)
    session = await _session(options={"row_count": 2})
    async for _ in engine.stream(user=_user("admin"), session=session, sql="SELECT 1"):
        pass
    # Lock must be free for the next query on the same session.
    assert not session.lock.locked()
