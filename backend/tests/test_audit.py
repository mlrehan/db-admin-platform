"""Phase 6 — audit logging tests (durable sink, query API, RBAC, immutability)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.adapters.base import ConnectionConfig
from app.db.engines import EngineType
from app.services.audit_service import AuditService, DatabaseAuditSink
from app.services.audit_sink import QueryAuditEvent


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _open_session(client: AsyncClient, headers: dict) -> str:
    conn = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={"name": "pg", "engine": "postgresql", "host": "h", "database": "d",
              "username": "u", "password": "secret-password-1", "options": {"row_count": 2}},
    )
    assert conn.status_code == 201, conn.text
    opened = await client.post(
        "/api/v1/sessions", headers=headers, json={"connection_id": conn.json()["id"]}
    )
    return opened.json()["id"]


# --- sink unit test ----------------------------------------------------------------------


async def test_sink_persists_event(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    sink = DatabaseAuditSink(sessionmaker)
    started = QueryAuditEvent.now()
    event = QueryAuditEvent(
        user_id=uuid.uuid4(),
        user_email="who@test.com",
        session_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        engine=EngineType.POSTGRESQL,
        statement="SELECT 1",
        category="read",
        destructive=False,
        success=True,
        duration_ms=1.2,
        started_at=started,
        finished_at=started,
        row_count=1,
    )
    await sink.record_query(event)

    async with sessionmaker() as session:
        rows = await AuditService(session).search()
    assert len(rows) == 1
    assert rows[0].statement == "SELECT 1"
    assert rows[0].user_email == "who@test.com"


# --- end-to-end: executing a query writes an audit record --------------------------------


async def test_query_creates_audit_record(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, admin)

    run = await client.post(
        f"/api/v1/sessions/{sid}/query", headers=admin, json={"sql": "SELECT * FROM t"}
    )
    assert run.status_code == 200, run.text

    logs = await client.get("/api/v1/audit/logs", headers=admin)
    assert logs.status_code == 200, logs.text
    entries = logs.json()
    assert any(e["statement"] == "SELECT * FROM t" and e["success"] for e in entries)
    assert entries[0]["category"] in {"read", "write", "ddl"}
    assert entries[0]["user_email"] == "admin@test.com"


async def test_destructive_query_audited(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    # An admin runs a destructive statement; it must be recorded as destructive in the audit.
    sid = await _open_session(client, admin)
    await client.post(f"/api/v1/sessions/{sid}/query", headers=admin, json={"sql": "DROP TABLE t"})

    logs = (await client.get("/api/v1/audit/logs?destructive=true", headers=admin)).json()
    assert any(e["destructive"] and e["statement"] == "DROP TABLE t" for e in logs)


# --- RBAC + immutability -----------------------------------------------------------------


async def test_viewer_cannot_read_audit(client: AsyncClient, seed_users) -> None:
    viewer = _auth(await _login(client, "viewer@test.com", "viewer-password-123"))
    resp = await client.get("/api/v1/audit/logs", headers=viewer)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTHORIZATION_ERROR"


async def test_developer_cannot_read_audit(client: AsyncClient, seed_users) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    await client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": "dev9@test.com", "password": "developer-pass-1", "role": "developer"},
    )
    dev = _auth(await _login(client, "dev9@test.com", "developer-pass-1"))
    assert (await client.get("/api/v1/audit/logs", headers=dev)).status_code == 403


async def test_no_audit_mutation_endpoints(client: AsyncClient, seed_users) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    # The audit log is immutable: no DELETE/PUT endpoints exist (405 Method Not Allowed).
    fake_id = uuid.uuid4()
    assert (await client.delete(f"/api/v1/audit/logs/{fake_id}", headers=admin)).status_code == 405
    assert (
        await client.put(f"/api/v1/audit/logs/{fake_id}", headers=admin, json={})
    ).status_code == 405


async def test_audit_filters(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, admin)
    await client.post(f"/api/v1/sessions/{sid}/query", headers=admin, json={"sql": "SELECT 1"})

    ok = await client.get("/api/v1/audit/logs?success=true&category=read", headers=admin)
    assert ok.status_code == 200
    assert all(e["success"] and e["category"] == "read" for e in ok.json())
