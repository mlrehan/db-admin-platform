"""Integration tests: access-grant CRUD + end-to-end enforcement at the API."""

from __future__ import annotations

from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _admin(client: AsyncClient) -> dict:
    return await _login(client, "admin@test.com", "admin-password-123")


async def _make_dba(client: AsyncClient, admin: dict) -> tuple[dict, str]:
    created = await client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": "dba7@test.com", "password": "dba-strong-pass-1", "role": "dba"},
    )
    assert created.status_code == 201, created.text
    return await _login(client, "dba7@test.com", "dba-strong-pass-1"), created.json()["id"]


async def _admin_conn(client: AsyncClient, admin: dict) -> str:
    """Admin creates an admin-owned connection (only admins may create connections)."""
    conn = await client.post(
        "/api/v1/connections",
        headers=admin,
        json={"name": "pg", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    assert conn.status_code == 201, conn.text
    return conn.json()["id"]


async def _grant_and_open(
    client: AsyncClient, admin: dict, user: dict, user_id: str, conn_id: str,
    operations: list[str], **scope: str,
) -> str:
    """Admin grants the user access, then the user opens a session on the shared connection."""
    grant = {"subject_type": "user", "subject_id": user_id, "connection_id": conn_id,
             "operations": operations, **scope}
    g = await client.post("/api/v1/access/grants", headers=admin, json=grant)
    assert g.status_code == 201, g.text
    opened = await client.post("/api/v1/sessions", headers=user, json={"connection_id": conn_id})
    assert opened.status_code == 201, opened.text
    return opened.json()["id"]


# --- grant CRUD --------------------------------------------------------------------------


async def test_grant_crud(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _admin(client)
    conn_id = await _admin_conn(client, admin)

    created = await client.post(
        "/api/v1/access/grants",
        headers=admin,
        json={"subject_type": "role", "subject_id": "developer", "connection_id": conn_id,
              "operations": ["select"], "database": "appdb", "table_name": "users"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["operations"] == ["SELECT"]
    grant_id = created.json()["id"]

    listed = await client.get(f"/api/v1/access/grants?connection_id={conn_id}", headers=admin)
    assert any(g["id"] == grant_id for g in listed.json())

    deleted = await client.delete(f"/api/v1/access/grants/{grant_id}", headers=admin)
    assert deleted.status_code == 204


async def test_invalid_operation_rejected(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _admin(client)
    conn_id = await _admin_conn(client, admin)
    resp = await client.post(
        "/api/v1/access/grants",
        headers=admin,
        json={"subject_type": "role", "subject_id": "developer", "connection_id": conn_id,
              "operations": ["DESTROY"]},
    )
    assert resp.status_code == 422


async def test_viewer_cannot_manage_grants(client: AsyncClient, seed_users) -> None:
    viewer = await _login(client, "viewer@test.com", "viewer-password-123")
    assert (await client.get("/api/v1/access/grants", headers=viewer)).status_code == 403


# --- enforcement -------------------------------------------------------------------------


async def test_grants_restrict_dba(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _admin(client)
    dba, dba_id = await _make_dba(client, admin)
    conn_id = await _admin_conn(client, admin)

    # Grant the DBA only SELECT on appdb.users, then they open a session on the shared conn.
    session_id = await _grant_and_open(
        client, admin, dba, dba_id, conn_id, ["SELECT"],
        database="appdb", table_name="users",
    )

    def run(sql):
        return client.post(
            f"/api/v1/sessions/{session_id}/query", headers=dba, json={"sql": sql}
        )

    # Allowed: SELECT on the granted table.
    ok = await run("SELECT * FROM users")
    assert ok.status_code == 200, ok.text

    # Denied: different table.
    denied_table = await run("SELECT * FROM orders")
    assert denied_table.status_code == 403
    assert denied_table.json()["error"]["code"] == "ACCESS_DENIED"

    # Denied: different operation, even though the DBA role normally allows DROP.
    denied_op = await run("DROP TABLE users")
    assert denied_op.status_code == 403
    assert denied_op.json()["error"]["code"] == "ACCESS_DENIED"


async def test_grants_filter_table_listing(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _admin(client)
    dba, dba_id = await _make_dba(client, admin)
    conn_id = await _admin_conn(client, admin)
    session_id = await _grant_and_open(
        client, admin, dba, dba_id, conn_id, ["SELECT"],
        database="appdb", table_name="users",
    )
    tables = await client.get(f"/api/v1/sessions/{session_id}/tables", headers=dba)
    assert tables.status_code == 200
    names = {t["name"] for t in tables.json()}
    assert names == {"users"}  # only the granted table is visible


async def test_admin_unaffected_by_grants(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _admin(client)
    # Admin owns a connection/session; grants never restrict an admin.
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "pg-a", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    sid = (await client.post("/api/v1/sessions", headers=admin,
                             json={"connection_id": conn.json()["id"]})).json()["id"]
    await client.post(
        "/api/v1/access/grants", headers=admin,
        json={"subject_type": "role", "subject_id": "admin", "connection_id": conn.json()["id"],
              "operations": ["SELECT"], "table_name": "nothing"},
    )
    resp = await client.post(
        f"/api/v1/sessions/{sid}/query", headers=admin, json={"sql": "DROP TABLE whatever"}
    )
    assert resp.status_code == 200  # admin bypasses grants
