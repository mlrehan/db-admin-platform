"""Server-level connection tests: optional database, list/switch databases."""

from __future__ import annotations

from httpx import AsyncClient


async def _login(client: AsyncClient) -> dict:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "admin-password-123"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_create_server_level_connection(client: AsyncClient, seed_users) -> None:
    headers = await _login(client)
    # No "database" field → server-level connection.
    resp = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "name": "pg-server", "engine": "postgresql", "host": "h",
            "username": "u", "password": "secret-password-1",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["database"] is None


async def test_blank_database_is_server_level(client: AsyncClient, seed_users) -> None:
    headers = await _login(client)
    resp = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "name": "pg-blank", "engine": "postgresql", "host": "h", "database": "   ",
            "username": "u", "password": "secret-password-1",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["database"] is None


async def test_database_specific_connection_still_works(client: AsyncClient, seed_users) -> None:
    headers = await _login(client)
    resp = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "name": "pg-specific", "engine": "postgresql", "host": "h", "database": "appdb",
            "username": "u", "password": "secret-password-1",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["database"] == "appdb"


async def _open_server_session(client: AsyncClient, headers: dict) -> str:
    conn = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={"name": "pg-srv", "engine": "postgresql", "host": "h",
              "username": "u", "password": "secret-password-1"},
    )
    assert conn.status_code == 201, conn.text
    opened = await client.post(
        "/api/v1/sessions", headers=headers, json={"connection_id": conn.json()["id"]}
    )
    assert opened.status_code == 201, opened.text
    return opened.json()["id"]


async def test_list_and_switch_databases(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = await _login(client)
    sid = await _open_server_session(client, headers)

    # List databases on the server.
    dbs = await client.get(f"/api/v1/sessions/{sid}/databases", headers=headers)
    assert dbs.status_code == 200, dbs.text
    names = {d["name"] for d in dbs.json()}
    assert {"appdb", "analytics", "reporting"} <= names

    # Switch the active database.
    switch = await client.post(
        f"/api/v1/sessions/{sid}/database", headers=headers, json={"database": "analytics"}
    )
    assert switch.status_code == 200, switch.text
    assert switch.json()["name"] == "analytics"

    # The session now reports the active database.
    sessions = await client.get("/api/v1/sessions", headers=headers)
    me = next(s for s in sessions.json() if s["id"] == sid)
    assert me["active_database"] == "analytics"

    # Schema browsing still works against the active database.
    schemas = await client.get(f"/api/v1/sessions/{sid}/schemas", headers=headers)
    assert schemas.status_code == 200
