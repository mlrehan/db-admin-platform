"""The end-to-end scenario from the bug report: an admin grants a user access to one
database server, restricted to one database and SELECT only — and it must actually work."""

from __future__ import annotations

from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_granted_user_can_use_shared_connection(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")

    # Admin owns a server-level connection.
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "company-pg", "engine": "postgresql", "host": "h",
              "username": "svc", "password": "secret-password-1"},
    )
    conn_id = conn.json()["id"]

    # A developer who does NOT own the connection. (Developer normally has write access — so
    # if the SELECT-only grant blocks their INSERT, that proves the GRANT, not just the role.)
    created = await client.post(
        "/api/v1/users", headers=admin,
        json={"email": "analyst@test.com", "password": "analyst-pass-1", "role": "developer"},
    )
    analyst_id = created.json()["id"]
    analyst = await _login(client, "analyst@test.com", "analyst-pass-1")

    # Before any grant: the analyst can't even see or open the connection.
    assert (await client.get("/api/v1/connections", headers=analyst)).json() == []
    blocked = await client.post("/api/v1/sessions", headers=analyst, json={"connection_id": conn_id})
    assert blocked.status_code == 404

    # Admin grants the analyst SELECT on database "appdb" only.
    g = await client.post(
        "/api/v1/access/grants", headers=admin,
        json={"subject_type": "user", "subject_id": analyst_id, "connection_id": conn_id,
              "operations": ["SELECT"], "database": "appdb"},
    )
    assert g.status_code == 201, g.text

    # Now the shared connection appears in the analyst's list, and they can open a session.
    listed = await client.get("/api/v1/connections", headers=analyst)
    assert {c["name"] for c in listed.json()} == {"company-pg"}
    opened = await client.post("/api/v1/sessions", headers=analyst, json={"connection_id": conn_id})
    assert opened.status_code == 201, opened.text
    sid = opened.json()["id"]

    # Only the granted database is visible.
    dbs = await client.get(f"/api/v1/sessions/{sid}/databases", headers=analyst)
    assert {d["name"] for d in dbs.json()} == {"appdb"}
    # Switching to a non-granted database is denied.
    bad = await client.post(f"/api/v1/sessions/{sid}/database", headers=analyst,
                            json={"database": "analytics"})
    assert bad.status_code == 403

    # Switch to the granted database and run SELECT → allowed.
    assert (await client.post(f"/api/v1/sessions/{sid}/database", headers=analyst,
                              json={"database": "appdb"})).status_code == 200
    ok = await client.post(f"/api/v1/sessions/{sid}/query", headers=analyst,
                           json={"sql": "SELECT * FROM users"})
    assert ok.status_code == 200, ok.text

    # A write is denied (SELECT-only grant) → ACCESS_DENIED.
    denied = await client.post(f"/api/v1/sessions/{sid}/query", headers=analyst,
                               json={"sql": "INSERT INTO users VALUES (1)"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ACCESS_DENIED"


async def test_logout_closes_open_sessions(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "c", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    await client.post("/api/v1/sessions", headers=admin, json={"connection_id": conn.json()["id"]})
    assert len((await client.get("/api/v1/sessions", headers=admin)).json()) == 1

    # Logging out closes all the user's live database sessions.
    assert (await client.post("/api/v1/auth/logout", headers=admin)).status_code == 204

    admin2 = await _login(client, "admin@test.com", "admin-password-123")
    assert (await client.get("/api/v1/sessions", headers=admin2)).json() == []


async def test_admin_can_edit_grant(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "c", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    grant = await client.post(
        "/api/v1/access/grants", headers=admin,
        json={"subject_type": "role", "subject_id": "developer",
              "connection_id": conn.json()["id"], "operations": ["SELECT"]},
    )
    gid = grant.json()["id"]
    # Edit: broaden to SELECT + INSERT and scope to a table.
    patched = await client.patch(
        f"/api/v1/access/grants/{gid}", headers=admin,
        json={"operations": ["select", "insert"], "table_name": "orders"},
    )
    assert patched.status_code == 200, patched.text
    assert set(patched.json()["operations"]) == {"SELECT", "INSERT"}
    assert patched.json()["table_name"] == "orders"
