"""Phase 5 — query HTTP API tests (buffered execute, RBAC, ownership, cancel)."""

from __future__ import annotations

from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _make_session(client: AsyncClient, headers: dict, *, row_count: int = 3) -> str:
    conn = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "name": "pg",
            "engine": "postgresql",
            "host": "h",
            "database": "d",
            "username": "u",
            "password": "secret-password-1",
            "options": {"row_count": row_count},
        },
    )
    assert conn.status_code == 201, conn.text
    opened = await client.post(
        "/api/v1/sessions", headers=headers, json={"connection_id": conn.json()["id"]}
    )
    assert opened.status_code == 201, opened.text
    return opened.json()["id"]


async def test_execute_select(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    session_id = await _make_session(client, headers, row_count=4)
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/query", headers=headers, json={"sql": "SELECT * FROM t"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["returns_rows"] is True
    assert body["row_count"] == 4
    assert [c["name"] for c in body["columns"]] == ["id", "label"]
    assert body["rows"][0] == [0, "row-0"]
    assert body["category"] == "read"


async def test_viewer_cannot_use_admin_session(client: AsyncClient, seed_users, fake_pg) -> None:
    # Ownership isolation: a viewer cannot run a query on an admin-owned session.
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    session_id = await _make_session(client, admin)
    viewer = _auth(await _login(client, "viewer@test.com", "viewer-password-123"))
    blocked = await client.post(
        f"/api/v1/sessions/{session_id}/query", headers=viewer, json={"sql": "SELECT 1"}
    )
    assert blocked.status_code == 404


async def test_granted_destructive_allowed(client: AsyncClient, seed_users, fake_pg) -> None:
    # A user explicitly granted DROP can run destructive DDL on the granted connection.
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "c", "engine": "postgresql", "host": "h", "database": "d",
              "username": "u", "password": "secret-password-1", "options": {"row_count": 3}},
    )
    conn_id = conn.json()["id"]
    dba_id = (await client.post(
        "/api/v1/users", headers=admin,
        json={"email": "dba2@test.com", "password": "dba-strong-pass-1", "role": "dba"},
    )).json()["id"]
    await client.post(
        "/api/v1/access/grants", headers=admin,
        json={"subject_type": "user", "subject_id": dba_id, "connection_id": conn_id,
              "operations": ["SELECT", "DROP"]},
    )
    dba = _auth(await _login(client, "dba2@test.com", "dba-strong-pass-1"))
    session_id = (await client.post(
        "/api/v1/sessions", headers=dba, json={"connection_id": conn_id}
    )).json()["id"]
    resp = await client.post(
        f"/api/v1/sessions/{session_id}/query", headers=dba, json={"sql": "DROP TABLE t"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["destructive"] is True
    assert resp.json()["category"] == "ddl"


async def test_execute_on_unknown_session(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    import uuid

    resp = await client.post(
        f"/api/v1/sessions/{uuid.uuid4()}/query", headers=headers, json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 404


async def test_cancel_unknown_query(client: AsyncClient, seed_users) -> None:
    import uuid

    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    resp = await client.post(f"/api/v1/queries/{uuid.uuid4()}/cancel", headers=headers)
    assert resp.status_code == 404


async def test_running_queries_empty(client: AsyncClient, seed_users) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    resp = await client.get("/api/v1/queries/running", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []
