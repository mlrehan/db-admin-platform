"""Phase 2 authentication & RBAC integration tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.auth.roles import Permission, Role, permissions_for, role_has_permission

# `asyncio_mode = "auto"` (pyproject) auto-marks async tests; sync tests run normally.


async def _login(client: AsyncClient, email: str, password: str):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_login_success_and_me(client: AsyncClient, seed_users) -> None:
    resp = await _login(client, "admin@test.com", "admin-password-123")
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "admin@test.com"
    assert me.json()["role"] == "admin"


async def test_login_wrong_password(client: AsyncClient, seed_users) -> None:
    resp = await _login(client, "admin@test.com", "wrong")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_ERROR"


async def test_login_unknown_user(client: AsyncClient, seed_users) -> None:
    resp = await _login(client, "nobody@test.com", "whatever-123456")
    assert resp.status_code == 401


async def test_me_requires_token(client: AsyncClient, seed_users) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_invalid_token_rejected(client: AsyncClient, seed_users) -> None:
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert resp.status_code == 401


async def test_refresh_rotates_tokens(client: AsyncClient, seed_users) -> None:
    login = (await _login(client, "viewer@test.com", "viewer-password-123")).json()
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_access_token_not_accepted_as_refresh(client: AsyncClient, seed_users) -> None:
    login = (await _login(client, "viewer@test.com", "viewer-password-123")).json()
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["access_token"]}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_WRONG_TYPE"


async def test_logout_revokes_all_tokens(client: AsyncClient, seed_users) -> None:
    login = (await _login(client, "viewer@test.com", "viewer-password-123")).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    # The previously-valid access token is now revoked (token_version bumped).
    after = await client.get("/api/v1/auth/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "TOKEN_REVOKED"


async def test_rbac_viewer_cannot_manage_users(client: AsyncClient, seed_users) -> None:
    login = (await _login(client, "viewer@test.com", "viewer-password-123")).json()
    resp = await client.get(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTHORIZATION_ERROR"


async def test_admin_can_create_and_list_users(client: AsyncClient, seed_users) -> None:
    login = (await _login(client, "admin@test.com", "admin-password-123")).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    created = await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "dev@test.com",
            "password": "developer-pass-123",
            "role": "developer",
            "full_name": "Dev User",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "developer"

    listing = await client.get("/api/v1/users", headers=headers)
    assert listing.status_code == 200
    emails = {u["email"] for u in listing.json()}
    assert {"admin@test.com", "viewer@test.com", "dev@test.com"} <= emails


async def test_duplicate_email_conflict(client: AsyncClient, seed_users) -> None:
    login = (await _login(client, "admin@test.com", "admin-password-123")).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    resp = await client.post(
        "/api/v1/users",
        headers=headers,
        json={"email": "viewer@test.com", "password": "another-pass-123", "role": "viewer"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_new_user_can_log_in(client: AsyncClient, seed_users) -> None:
    admin = (await _login(client, "admin@test.com", "admin-password-123")).json()
    await client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
        json={"email": "dba@test.com", "password": "dba-strong-pass-1", "role": "dba"},
    )
    resp = await _login(client, "dba@test.com", "dba-strong-pass-1")
    assert resp.status_code == 200


# --- pure RBAC unit checks ---------------------------------------------------------------


def test_admin_has_all_permissions() -> None:
    assert permissions_for(Role.ADMIN) == frozenset(Permission)


def test_role_permission_matrix() -> None:
    # Admin-controlled model: only Admin manages connections/users/audit. Non-admin roles
    # have just CONNECTION_USE + SCHEMA_READ; their actual query access comes from grants.
    assert role_has_permission(Role.ADMIN, Permission.CONNECTION_MANAGE)
    for role in (Role.DBA, Role.DEVELOPER, Role.VIEWER):
        assert not role_has_permission(role, Permission.CONNECTION_MANAGE)
        assert not role_has_permission(role, Permission.USER_MANAGE)
        assert not role_has_permission(role, Permission.AUDIT_READ)
        assert role_has_permission(role, Permission.CONNECTION_USE)
        assert role_has_permission(role, Permission.SCHEMA_READ)
