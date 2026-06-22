"""Phase 3 — saved connections (service + API) and live-session endpoints."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.adapters import registry
from app.db.engines import EngineType
from app.models.connection import Connection
from app.schemas.connection import ConnectionCreate
from app.security.encryption import get_credential_cipher
from app.services.connection_service import ConnectionService
from tests.fakes import FakeAdapter


@pytest.fixture
def fake_pg() -> Iterator[None]:
    registry.register_adapter(EngineType.POSTGRESQL, FakeAdapter)
    yield
    registry._registry.pop(EngineType.POSTGRESQL, None)  # noqa: SLF001


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _payload(**over) -> dict:
    base = {
        "name": "primary-pg",
        "engine": "postgresql",
        "host": "db.internal",
        "database": "app",
        "username": "svc",
        "password": "super-secret-pw",
    }
    base.update(over)
    return base


# --- service-level (encryption guarantees) ----------------------------------------------


@pytest_asyncio.fixture
async def admin_id(seed_users) -> object:
    return seed_users["admin"].id


async def test_service_encrypts_and_roundtrips(
    sessionmaker: async_sessionmaker[AsyncSession], admin_id
) -> None:
    cipher = get_credential_cipher()
    async with sessionmaker() as session:
        service = ConnectionService(session, cipher)
        conn = await service.create(
            owner_id=admin_id,
            data=ConnectionCreate(**_payload(password="my-db-password")),
        )
        await session.commit()
        conn_id = conn.id

    # The stored blob must not contain the plaintext password.
    async with sessionmaker() as session:
        row = (
            await session.execute(select(Connection).where(Connection.id == conn_id))
        ).scalar_one()
        assert "my-db-password" not in row.encrypted_credentials
        assert row.encrypted_credentials.startswith("v1:")

        # resolve_config decrypts back to the original secret (server-side only).
        config = ConnectionService(session, cipher).resolve_config(row)
        assert config.password == "my-db-password"
        assert config.port == 5432  # engine default applied


# --- API-level --------------------------------------------------------------------------


async def test_create_connection_hides_credentials(client: AsyncClient, seed_users) -> None:
    headers = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    resp = await client.post("/api/v1/connections", headers=headers, json=_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "password" not in body and "encrypted_credentials" not in body
    assert body["engine"] == "postgresql"
    assert body["port"] == 5432


async def test_non_admin_cannot_create_connections(client: AsyncClient, seed_users) -> None:
    # Admin-controlled model: only an administrator may create connections.
    viewer = _auth(await _login(client, "viewer@test.com", "viewer-password-123"))
    resp = await client.post("/api/v1/connections", headers=viewer, json=_payload())
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTHORIZATION_ERROR"
    # And they see no connections until an admin shares one with them.
    listed = await client.get("/api/v1/connections", headers=viewer)
    assert listed.status_code == 200
    assert listed.json() == []


async def test_connection_owner_isolation(client: AsyncClient, seed_users) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    created = await client.post("/api/v1/connections", headers=admin, json=_payload())
    conn_id = created.json()["id"]

    viewer = _auth(await _login(client, "viewer@test.com", "viewer-password-123"))
    # Viewer can't see another owner's connection (404, not 403 — no existence leak).
    got = await client.get(f"/api/v1/connections/{conn_id}", headers=viewer)
    assert got.status_code == 404


async def test_update_and_delete_connection(client: AsyncClient, seed_users) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    conn_id = (
        await client.post("/api/v1/connections", headers=admin, json=_payload())
    ).json()["id"]

    patched = await client.patch(
        f"/api/v1/connections/{conn_id}",
        headers=admin,
        json={"host": "new-host", "password": "rotated-pw"},
    )
    assert patched.status_code == 200
    assert patched.json()["host"] == "new-host"

    deleted = await client.delete(f"/api/v1/connections/{conn_id}", headers=admin)
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/v1/connections/{conn_id}", headers=admin)
    ).status_code == 404


async def test_duplicate_name_conflict(client: AsyncClient, seed_users) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    await client.post("/api/v1/connections", headers=admin, json=_payload())
    dup = await client.post("/api/v1/connections", headers=admin, json=_payload())
    assert dup.status_code == 409


async def test_test_endpoint_unsupported_engine(client: AsyncClient, seed_users) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    conn_id = (
        await client.post("/api/v1/connections", headers=admin, json=_payload())
    ).json()["id"]
    # No adapter registered → typed UNSUPPORTED_ENGINE.
    resp = await client.post(f"/api/v1/connections/{conn_id}/test", headers=admin)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_ENGINE"


async def test_test_endpoint_ok_with_adapter(
    client: AsyncClient, seed_users, fake_pg: None
) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    conn_id = (
        await client.post("/api/v1/connections", headers=admin, json=_payload())
    ).json()["id"]
    resp = await client.post(f"/api/v1/connections/{conn_id}/test", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert resp.json()["server_version"] == "fake-1.0"


async def test_open_list_and_close_session(
    client: AsyncClient, seed_users, fake_pg: None
) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    conn_id = (
        await client.post("/api/v1/connections", headers=admin, json=_payload())
    ).json()["id"]

    opened = await client.post(
        "/api/v1/sessions", headers=admin, json={"connection_id": conn_id}
    )
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    assert opened.json()["connected"] is True

    listed = await client.get("/api/v1/sessions", headers=admin)
    assert {s["id"] for s in listed.json()} == {session_id}

    closed = await client.delete(f"/api/v1/sessions/{session_id}", headers=admin)
    assert closed.status_code == 204
    assert (await client.get("/api/v1/sessions", headers=admin)).json() == []


async def test_session_not_visible_to_other_user(
    client: AsyncClient, seed_users, fake_pg: None
) -> None:
    admin = _auth(await _login(client, "admin@test.com", "admin-password-123"))
    conn_id = (
        await client.post("/api/v1/connections", headers=admin, json=_payload())
    ).json()["id"]
    session_id = (
        await client.post("/api/v1/sessions", headers=admin, json={"connection_id": conn_id})
    ).json()["id"]

    viewer = _auth(await _login(client, "viewer@test.com", "viewer-password-123"))
    assert (
        await client.get(f"/api/v1/sessions/{session_id}", headers=viewer)
    ).status_code == 404
    assert (await client.get("/api/v1/sessions", headers=viewer)).json() == []
