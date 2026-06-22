"""Phase 5 — WebSocket streaming endpoint integration tests.

Uses Starlette's ``TestClient`` (which drives the app in its own event loop) against a
file-based SQLite database so state is shared across loops. The lifespan runs, so the
orchestrator and query engine are real; we re-register the FakeAdapter *after* startup to
override the built-in PostgreSQL adapter for the test.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from app.auth.roles import Role
from app.db.adapters import registry
from app.db.base import Base
from app.db.engines import EngineType
from app.db.session import get_session
from app.main import create_app
from app.models.user import User
from app.security import password as pwd
from tests.fakes import FakeAdapter

_EMAIL = "admin@test.com"
_PASSWORD = "admin-password-123"


@pytest.fixture
def ws_app(tmp_path) -> Iterator[object]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}"

    async def _seed() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            session.add(
                User(
                    email=_EMAIL,
                    hashed_password=pwd.hash_password(_PASSWORD),
                    role=Role.ADMIN,
                    is_active=True,
                )
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())

    app = create_app()

    async def _override_get_session():
        engine = create_async_engine(url)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            try:
                yield session
                await session.commit()
            finally:
                await session.close()
                await engine.dispose()

    app.dependency_overrides[get_session] = _override_get_session
    yield app
    app.dependency_overrides.clear()


def _login_and_open_session(client: TestClient) -> tuple[str, str]:
    tokens = client.post(
        "/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD}
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    conn = client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "name": "pg", "engine": "postgresql", "host": "h", "database": "d",
            "username": "u", "password": "secret-password-1", "options": {"row_count": 5},
        },
    ).json()
    session = client.post(
        "/api/v1/sessions", headers=headers, json={"connection_id": conn["id"]}
    ).json()
    return tokens["access_token"], session["id"]


def test_ws_streams_select(ws_app) -> None:
    with TestClient(ws_app) as client:
        # Override the real PG adapter (registered by lifespan) with the in-memory fake.
        registry.register_adapter(EngineType.POSTGRESQL, FakeAdapter)
        try:
            token, session_id = _login_and_open_session(client)
            with client.websocket_connect(
                f"/api/v1/ws/sessions/{session_id}/query?token={token}"
            ) as ws:
                ws.send_json({"action": "execute", "sql": "SELECT * FROM t", "batch_size": 2})
                events = []
                while True:
                    event = ws.receive_json()
                    events.append(event)
                    if event["type"] in ("end", "error"):
                        break
            types = [e["type"] for e in events]
            assert types[0] == "accepted"
            assert "columns" in types
            assert "rows" in types
            assert types[-1] == "end"
            total = sum(len(e["rows"]) for e in events if e["type"] == "rows")
            assert total == 5
            assert events[-1]["row_count"] == 5
        finally:
            registry._registry.pop(EngineType.POSTGRESQL, None)  # noqa: SLF001


def test_ws_rejects_missing_token(ws_app) -> None:
    with TestClient(ws_app) as client:
        with client.websocket_connect(
            f"/api/v1/ws/sessions/{uuid.uuid4()}/query"
        ) as ws:
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "AUTHENTICATION_ERROR"


def test_ws_unknown_action(ws_app) -> None:
    with TestClient(ws_app) as client:
        registry.register_adapter(EngineType.POSTGRESQL, FakeAdapter)
        try:
            token, session_id = _login_and_open_session(client)
            with client.websocket_connect(
                f"/api/v1/ws/sessions/{session_id}/query?token={token}"
            ) as ws:
                ws.send_json({"action": "frobnicate"})
                event = ws.receive_json()
                assert event["type"] == "error"
                assert event["code"] == "UNKNOWN_ACTION"
        finally:
            registry._registry.pop(EngineType.POSTGRESQL, None)  # noqa: SLF001
