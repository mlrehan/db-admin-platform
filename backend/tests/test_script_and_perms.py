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


async def test_script_runs_in_one_session_returns_all_sets(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    # The whole script runs in one session; every result set is returned plus row-count
    # messages from non-returning statements.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    _, sid = await _admin_session(client, admin, row_count=2)
    resp = await client.post(
        f"/api/v1/sessions/{sid}/script", headers=admin,
        json={"sql": "INSERT INTO t VALUES (1); SELECT * FROM t; UPDATE t SET x=1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    # One row-returning result set (the SELECT, 2 rows) + two row-count messages.
    row_sets = [s for s in body["statements"] if s["returns_rows"]]
    assert len(row_sets) == 1 and row_sets[0]["row_count"] == 2
    assert len(body["statements"]) == 3


async def test_script_denied_as_a_unit_before_execution(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    # A user granted only SELECT: a script that also DELETEs is rejected as a unit *before*
    # anything runs (no partial execution of the non-granted statement).
    admin = await _login(client, "admin@test.com", "admin-password-123")
    user, _, sid = await _granted_user(client, admin, "viewer", ["SELECT"])
    resp = await client.post(
        f"/api/v1/sessions/{sid}/script", headers=user,
        json={"sql": "SELECT 1; DELETE FROM t; SELECT 2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["statements"][0]["error_code"] == "ACCESS_DENIED"
    assert "DELETE" in body["statements"][0]["error"]


async def _granted_user_with_routines(
    client: AsyncClient, admin: dict, routines: dict
) -> tuple[dict, str]:
    """A viewer granted SELECT on appdb, on a connection whose adapter exposes ``routines``."""
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "rtn-pg", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1", "options": {"routines": routines}},
    )
    conn_id = conn.json()["id"]
    uid = (await client.post(
        "/api/v1/users", headers=admin,
        json={"email": "rtnuser@test.com", "password": "user-strong-pass-1", "role": "viewer"},
    )).json()["id"]
    await client.post("/api/v1/access/grants", headers=admin,
                      json={"subject_type": "user", "subject_id": uid, "connection_id": conn_id,
                            "operations": ["SELECT"], "database": "appdb"})
    user = await _login(client, "rtnuser@test.com", "user-strong-pass-1")
    sid = (await client.post("/api/v1/sessions", headers=user,
                             json={"connection_id": conn_id})).json()["id"]
    return user, sid


async def test_readonly_routine_exec_allowed_with_select(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    # A SELECT user may EXEC a routine whose body only reads tables they can read.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    user, sid = await _granted_user_with_routines(
        client, admin, {"usp_test": "CREATE PROCEDURE usp_test AS BEGIN SELECT * FROM users; END"}
    )
    resp = await client.post(f"/api/v1/sessions/{sid}/script", headers=user,
                             json={"sql": "EXEC usp_test;"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


async def test_writing_routine_exec_denied(client: AsyncClient, seed_users, fake_pg) -> None:
    # A routine that writes must NOT be executable by a SELECT-only user.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    user, sid = await _granted_user_with_routines(
        client, admin, {"usp_del": "CREATE PROCEDURE usp_del AS BEGIN DELETE FROM users; END"}
    )
    resp = await client.post(f"/api/v1/sessions/{sid}/script", headers=user,
                             json={"sql": "EXEC usp_del;"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["statements"][0]["error_code"] == "ACCESS_DENIED"


async def test_unknown_routine_exec_denied(client: AsyncClient, seed_users, fake_pg) -> None:
    # A routine whose body can't be fetched/verified is denied (fail-closed).
    admin = await _login(client, "admin@test.com", "admin-password-123")
    user, sid = await _granted_user_with_routines(client, admin, {})
    resp = await client.post(f"/api/v1/sessions/{sid}/script", headers=user,
                             json={"sql": "EXEC mystery_proc;"})
    assert resp.json()["success"] is False
    assert resp.json()["statements"][0]["error_code"] == "ACCESS_DENIED"


async def test_admin_execs_routine_without_validation(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    # Admins bypass routine validation entirely.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "admin-rtn", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    sid = (await client.post("/api/v1/sessions", headers=admin,
                             json={"connection_id": conn.json()["id"]})).json()["id"]
    resp = await client.post(f"/api/v1/sessions/{sid}/script", headers=admin,
                             json={"sql": "EXEC any_proc;"})
    assert resp.status_code == 200 and resp.json()["success"] is True


async def test_temp_table_read_script_allowed_with_select(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    # A read-only script that builds temp tables from granted source tables is allowed for a
    # SELECT-only user — temp tables need no grant.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    user, _, sid = await _granted_user(client, admin, "viewer", ["SELECT"])
    script = (
        "SELECT id INTO #a FROM users; "
        "SELECT id INTO #b FROM orders; "
        "SELECT * FROM #a JOIN #b ON #a.id = #b.id; "
        "DROP TABLE #a; DROP TABLE #b;"
    )
    resp = await client.post(
        f"/api/v1/sessions/{sid}/script", headers=user, json={"sql": script}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
