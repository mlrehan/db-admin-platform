"""Phase 6 — Schema Explorer API tests (via FakeAdapter's canned schema)."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


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
        json={
            "name": "pg", "engine": "postgresql", "host": "h", "database": "d",
            "username": "u", "password": "secret-password-1",
        },
    )
    assert conn.status_code == 201, conn.text
    opened = await client.post(
        "/api/v1/sessions", headers=headers, json={"connection_id": conn.json()["id"]}
    )
    assert opened.status_code == 201, opened.text
    return opened.json()["id"]


async def test_list_schemas(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, headers)
    resp = await client.get(f"/api/v1/sessions/{sid}/schemas", headers=headers)
    assert resp.status_code == 200, resp.text
    names = {s["name"] for s in resp.json()}
    assert {"public", "reporting"} <= names
    assert any(s["is_default"] for s in resp.json())


async def test_list_tables(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, headers)
    resp = await client.get(f"/api/v1/sessions/{sid}/tables?schema=public", headers=headers)
    assert resp.status_code == 200
    kinds = {t["name"]: t["kind"] for t in resp.json()}
    assert kinds["users"] == "table"
    assert kinds["active_users"] == "view"


async def test_describe_table(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, headers)
    resp = await client.get(f"/api/v1/sessions/{sid}/tables/users", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["primary_key"] == ["id"]
    col_names = [c["name"] for c in body["columns"]]
    assert col_names == ["id", "label"]
    assert body["columns"][0]["primary_key"] is True
    assert body["indexes"][0]["name"] == "ix_users_label"


async def test_list_routines(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, headers)
    resp = await client.get(f"/api/v1/sessions/{sid}/routines", headers=headers)
    assert resp.status_code == 200
    kinds = {r["name"]: r["kind"] for r in resp.json()}
    assert kinds["recalc_totals"] == "procedure"
    assert kinds["user_count"] == "function"


async def test_schema_requires_session_ownership(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, admin)
    viewer = _auth(await _login(client, "viewer@test.com", "viewer-password-123"))
    resp = await client.get(f"/api/v1/sessions/{sid}/schemas", headers=viewer)
    assert resp.status_code == 404


async def test_schema_unknown_session(client: AsyncClient, seed_users) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    resp = await client.get(f"/api/v1/sessions/{uuid.uuid4()}/schemas", headers=headers)
    assert resp.status_code == 404


# --- database creation -------------------------------------------------------------------


async def test_admin_can_create_database(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, headers)
    resp = await client.post(
        f"/api/v1/sessions/{sid}/databases", headers=headers, json={"name": "new_db"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "new_db"


async def _make_developer_with_grant(
    client: AsyncClient, admin: dict, operations: list[str], **scope
) -> tuple[dict, str]:
    """Create a connection owned by admin, a developer, and a grant; return (dev_headers, sid)."""
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "srv", "engine": "postgresql", "host": "h",
              "username": "u", "password": "secret-password-1"},
    )
    conn_id = conn.json()["id"]
    created = await client.post(
        "/api/v1/users", headers=admin,
        json={"email": "dev@test.com", "password": "dev-password-1", "role": "developer"},
    )
    dev_id = created.json()["id"]
    await client.post(
        "/api/v1/access/grants", headers=admin,
        json={"subject_type": "user", "subject_id": dev_id, "connection_id": conn_id,
              "operations": operations, **scope},
    )
    dev = _auth(await _login(client, "dev@test.com", "dev-password-1"))
    opened = await client.post("/api/v1/sessions", headers=dev, json={"connection_id": conn_id})
    assert opened.status_code == 201, opened.text
    return dev, opened.json()["id"]


async def test_non_admin_without_create_grant_cannot_create_database(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    dev, sid = await _make_developer_with_grant(client, admin, ["SELECT"], database="appdb")
    resp = await client.post(
        f"/api/v1/sessions/{sid}/databases", headers=dev, json={"name": "sneaky_db"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCESS_DENIED"


async def test_non_admin_with_connection_create_grant_can_create_database(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    # Broad CREATE on the whole connection (no database/table scope).
    dev, sid = await _make_developer_with_grant(client, admin, ["CREATE"])
    resp = await client.post(
        f"/api/v1/sessions/{sid}/databases", headers=dev, json={"name": "dev_db"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "dev_db"


# --- system schema hiding (feature: other users never see system files) ------------------


async def test_admin_sees_system_schema(client: AsyncClient, seed_users, fake_pg) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    sid = await _open_session(client, headers)
    resp = await client.get(f"/api/v1/sessions/{sid}/schemas", headers=headers)
    names = {s["name"] for s in resp.json()}
    assert "pg_catalog" in names  # admin retains full visibility


async def test_non_admin_never_sees_system_schema(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    dev, sid = await _make_developer_with_grant(client, admin, ["SELECT"], database="appdb")
    resp = await client.get(f"/api/v1/sessions/{sid}/schemas", headers=dev)
    assert resp.status_code == 200, resp.text
    names = {s["name"] for s in resp.json()}
    assert "pg_catalog" not in names  # system schema hidden
    assert "public" in names  # ordinary schemas still visible
