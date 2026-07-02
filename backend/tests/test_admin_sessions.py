"""Admin oversight of all users' live sessions: list, disconnect one, disconnect all."""

from __future__ import annotations

from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _open_session_for(client: AsyncClient, admin: dict, *, owner: dict) -> tuple[str, str]:
    """Admin creates a connection and shares it (SELECT) so ``owner`` can open a session."""
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "shared", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    conn_id = conn.json()["id"]
    sid = (await client.post("/api/v1/sessions", headers=owner,
                             json={"connection_id": conn_id})).json()
    return conn_id, sid["id"] if isinstance(sid, dict) else sid


async def test_admin_lists_all_sessions(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    # Admin opens a session of their own.
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "c1", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    await client.post("/api/v1/sessions", headers=admin,
                      json={"connection_id": conn.json()["id"]})

    resp = await client.get("/api/v1/sessions/admin/all", headers=admin)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert rows
    row = rows[0]
    for key in ("user_email", "connection_name", "engine", "database", "idle", "idle_seconds"):
        assert key in row
    assert row["user_email"] == "admin@test.com"
    assert row["connection_name"] == "c1"


async def test_non_admin_cannot_list_all_sessions(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    viewer = await _login(client, "viewer@test.com", "viewer-password-123")
    resp = await client.get("/api/v1/sessions/admin/all", headers=viewer)
    assert resp.status_code == 403


async def test_admin_disconnects_single_session(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "c2", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    sid = (await client.post("/api/v1/sessions", headers=admin,
                             json={"connection_id": conn.json()["id"]})).json()["id"]
    resp = await client.delete(f"/api/v1/sessions/admin/{sid}", headers=admin)
    assert resp.status_code == 204
    # It's gone from the admin listing.
    rows = (await client.get("/api/v1/sessions/admin/all", headers=admin)).json()
    assert not any(r["id"] == sid for r in rows)


async def test_admin_disconnects_all_but_self(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "c3", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    own_sid = (await client.post("/api/v1/sessions", headers=admin,
                                 json={"connection_id": conn.json()["id"]})).json()["id"]
    # exclude_self keeps the admin's own session alive.
    resp = await client.post("/api/v1/sessions/admin/close-all", headers=admin,
                             json={"exclude_self": True})
    assert resp.status_code == 200, resp.text
    rows = (await client.get("/api/v1/sessions/admin/all", headers=admin)).json()
    assert any(r["id"] == own_sid for r in rows)  # own session survived
