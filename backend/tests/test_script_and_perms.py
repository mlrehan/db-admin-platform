"""Multi-statement script execution + deep, layered permission checks (role + grants)."""

from __future__ import annotations

from httpx import AsyncClient

from app.services.sql_guard import split_sql_statements


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _admin_session(client: AsyncClient, headers: dict, *, row_count: int = 3) -> tuple[str, str]:
    """Admin creates an admin-owned connection and opens a session on it."""
    conn = await client.post(
        "/api/v1/connections",
        headers=headers,
        json={"name": "mine", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1", "options": {"row_count": row_count}},
    )
    assert conn.status_code == 201, conn.text
    opened = await client.post(
        "/api/v1/sessions", headers=headers, json={"connection_id": conn.json()["id"]}
    )
    assert opened.status_code == 201, opened.text
    return conn.json()["id"], opened.json()["id"]


async def _granted_user(
    client: AsyncClient, admin: dict, role: str, operations: list[str], *,
    database: str | None = "appdb", table_name: str | None = None,
) -> tuple[dict, str, str]:
    """Admin creates a connection + a user + a grant; returns (user headers, conn_id, sid)."""
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "shared-pg", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1", "options": {"row_count": 3}},
    )
    conn_id = conn.json()["id"]
    created = await client.post(
        "/api/v1/users", headers=admin,
        json={"email": f"u_{role}@test.com", "password": "user-strong-pass-1", "role": role},
    )
    uid = created.json()["id"]
    grant = {"subject_type": "user", "subject_id": uid, "connection_id": conn_id,
             "operations": operations}
    if database:
        grant["database"] = database
    if table_name:
        grant["table_name"] = table_name
    g = await client.post("/api/v1/access/grants", headers=admin, json=grant)
    assert g.status_code == 201, g.text
    user = await _login(client, f"u_{role}@test.com", "user-strong-pass-1")
    opened = await client.post("/api/v1/sessions", headers=user, json={"connection_id": conn_id})
    assert opened.status_code == 201, opened.text
    return user, conn_id, opened.json()["id"]


# --- splitter unit -----------------------------------------------------------------------


def test_split_preserves_original_statements() -> None:
    # Semicolons inside strings and comments must NOT split the batch.
    sql = "SELECT 1;\nINSERT INTO t VALUES (';');\n-- a comment ; not a split\nUPDATE t SET x=1"
    parts = split_sql_statements(sql)
    assert len(parts) == 3
    assert parts[0] == "SELECT 1"
    assert parts[1] == "INSERT INTO t VALUES (';')"          # semicolon in string preserved
    assert parts[2].endswith("UPDATE t SET x=1")             # comment kept with the statement


# --- grant-driven access (admin decides; users get what they are given) ------------------


async def test_granted_user_does_exactly_what_granted(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    # Admin grants a developer SELECT + INSERT on appdb (any table).
    dev, _, sid = await _granted_user(client, admin, "developer", ["SELECT", "INSERT"])

    def run(sql):
        return client.post(f"/api/v1/sessions/{sid}/query", headers=dev, json={"sql": sql})

    assert (await run("SELECT * FROM t")).status_code == 200          # granted
    assert (await run("INSERT INTO t VALUES (1)")).status_code == 200  # granted
    drop = await run("DROP TABLE t")                                   # NOT granted
    assert drop.status_code == 403
    assert drop.json()["error"]["code"] == "ACCESS_DENIED"


async def test_grant_is_authoritative_over_role(client: AsyncClient, seed_users, fake_pg) -> None:
    # A SELECT-only grant means the user can ONLY select, regardless of role label.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    dev, _, sid = await _granted_user(
        client, admin, "developer", ["SELECT"], database="appdb", table_name="users"
    )
    assert (await client.post(f"/api/v1/sessions/{sid}/query", headers=dev,
                              json={"sql": "SELECT * FROM users"})).status_code == 200
    denied = await client.post(f"/api/v1/sessions/{sid}/query", headers=dev,
                               json={"sql": "INSERT INTO users VALUES (1)"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ACCESS_DENIED"


# --- script execution --------------------------------------------------------------------


async def test_script_runs_multiple_statements(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    _, sid = await _admin_session(client, admin, row_count=2)
    resp = await client.post(
        f"/api/v1/sessions/{sid}/script", headers=admin,
        json={"sql": "INSERT INTO t VALUES (1); SELECT * FROM t; UPDATE t SET x=1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert len(body["statements"]) == 3
    # The SELECT statement carries rows; the others report affected counts.
    select_stmt = body["statements"][1]
    assert select_stmt["returns_rows"] is True
    assert select_stmt["row_count"] == 2


async def test_script_stops_on_error(client: AsyncClient, seed_users, fake_pg) -> None:
    # A user granted only SELECT: the script halts at the first non-granted statement.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    user, _, sid = await _granted_user(client, admin, "viewer", ["SELECT"])
    resp = await client.post(
        f"/api/v1/sessions/{sid}/script", headers=user,
        json={"sql": "SELECT 1; DELETE FROM t; SELECT 2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["statements"][0]["success"] is True            # SELECT ran
    assert body["statements"][1]["success"] is False           # DELETE not granted
    assert body["statements"][1]["error_code"] == "ACCESS_DENIED"
    assert len(body["statements"]) == 2                         # stopped; SELECT 2 never ran
