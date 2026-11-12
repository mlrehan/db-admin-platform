"""IntelliSense catalog endpoint + the shared, single-flight, DDL-invalidated metadata cache."""

from __future__ import annotations

import asyncio
import uuid

from httpx import AsyncClient

from app.services.metadata_cache import MetadataCache


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _session(client: AsyncClient, headers: dict) -> str:
    conn = await client.post(
        "/api/v1/connections", headers=headers,
        json={"name": f"pg-{uuid.uuid4().hex[:6]}", "engine": "postgresql", "host": "h",
              "database": "appdb", "username": "u", "password": "secret-password-1"},
    )
    assert conn.status_code == 201, conn.text
    opened = await client.post("/api/v1/sessions", headers=headers,
                               json={"connection_id": conn.json()["id"]})
    return opened.json()["id"]


async def test_catalog_returns_tables_with_columns(client: AsyncClient, seed_users, fake_pg) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    sid = await _session(client, admin)
    resp = await client.get(f"/api/v1/sessions/{sid}/catalog", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {o["name"]: o for o in body["objects"]}
    assert {"users", "orders", "active_users"} <= set(names)
    assert names["active_users"]["kind"] == "view"
    assert [c["name"] for c in names["users"]["columns"]][:2] == ["id", "label"]
    assert body["dialect"] == "postgresql" and body["default_schema"] == "public"


async def test_catalog_is_cached_and_invalidated_by_ddl(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    from app.services.metadata_cache import metadata_cache

    admin = await _login(client, "admin@test.com", "admin-password-123")
    sid = await _session(client, admin)
    for _ in range(3):
        assert (await client.get(f"/api/v1/sessions/{sid}/catalog", headers=admin)).status_code == 200
    stats = metadata_cache.stats()
    assert stats["misses"] == 1 and stats["hits"] == 2  # one DB round trip for three reads

    # DDL through the platform drops the cached catalog → the next read reloads it.
    await client.post(f"/api/v1/sessions/{sid}/script", headers=admin,
                      json={"sql": "CREATE TABLE t2 (id int)"})
    await client.get(f"/api/v1/sessions/{sid}/catalog", headers=admin)
    assert metadata_cache.stats()["misses"] == 2


async def test_catalog_filtered_by_grants(client: AsyncClient, seed_users, fake_pg) -> None:
    # A non-admin granted only the `orders` table sees only `orders` in IntelliSense.
    admin = await _login(client, "admin@test.com", "admin-password-123")
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "shared-cat", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    conn_id = conn.json()["id"]
    uid = (await client.post("/api/v1/users", headers=admin, json={
        "email": "catuser@test.com", "password": "user-strong-pass-1", "role": "viewer",
    })).json()["id"]
    await client.post("/api/v1/access/grants", headers=admin, json={
        "subject_type": "user", "subject_id": uid, "connection_id": conn_id,
        "operations": ["SELECT"], "databases": ["appdb"], "tables": ["orders"],
    })
    user = await _login(client, "catuser@test.com", "user-strong-pass-1")
    sid = (await client.post("/api/v1/sessions", headers=user,
                             json={"connection_id": conn_id})).json()["id"]
    body = (await client.get(f"/api/v1/sessions/{sid}/catalog", headers=user)).json()
    assert [o["name"] for o in body["objects"]] == ["orders"]


async def test_cache_single_flight_shares_one_load() -> None:
    cache = MetadataCache(ttl_seconds=60)
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return ["x"]

    results = await asyncio.gather(*(cache.get_or_load(("c", "db", "k"), loader) for _ in range(50)))
    assert calls == 1 and all(r == ["x"] for r in results)


async def test_cache_invalidation_during_load_is_not_stored() -> None:
    cache = MetadataCache(ttl_seconds=60)

    async def slow():
        await asyncio.sleep(0.05)
        return "stale"

    task = asyncio.create_task(cache.get_or_load(("c", "db", "k"), slow))
    await asyncio.sleep(0.01)
    cache.invalidate("c")  # DDL happened while loading
    assert await task == "stale"
    assert cache.stats()["entries"] == 0  # the racing result was not cached


async def test_cache_errors_are_not_cached() -> None:
    cache = MetadataCache(ttl_seconds=60)

    async def boom():
        raise RuntimeError("db down")

    for _ in range(2):
        try:
            await cache.get_or_load(("c", "db", "k"), boom)
        except RuntimeError:
            pass
    assert cache.stats()["misses"] == 2 and cache.stats()["entries"] == 0


async def test_revoked_grant_takes_effect_immediately_despite_grant_cache(
    client: AsyncClient, seed_users, fake_pg
) -> None:
    admin = await _login(client, "admin@test.com", "admin-password-123")
    conn = await client.post(
        "/api/v1/connections", headers=admin,
        json={"name": "revoke-pg", "engine": "postgresql", "host": "h", "database": "appdb",
              "username": "u", "password": "secret-password-1"},
    )
    conn_id = conn.json()["id"]
    uid = (await client.post("/api/v1/users", headers=admin, json={
        "email": "revokee@test.com", "password": "user-strong-pass-1", "role": "viewer",
    })).json()["id"]
    grant = (await client.post("/api/v1/access/grants", headers=admin, json={
        "subject_type": "user", "subject_id": uid, "connection_id": conn_id,
        "operations": ["SELECT"], "databases": ["appdb"],
    })).json()
    user = await _login(client, "revokee@test.com", "user-strong-pass-1")
    sid = (await client.post("/api/v1/sessions", headers=user, json={"connection_id": conn_id})).json()["id"]
    ok = await client.post(f"/api/v1/sessions/{sid}/query", headers=user, json={"sql": "SELECT * FROM users"})
    assert ok.status_code == 200  # grant now cached

    # Admin revokes; the very next request must be denied (no 10 s window).
    assert (await client.delete(f"/api/v1/access/grants/{grant['id']}", headers=admin)).status_code == 204
    denied = await client.post(f"/api/v1/sessions/{sid}/query", headers=user, json={"sql": "SELECT * FROM users"})
    assert denied.status_code == 403
