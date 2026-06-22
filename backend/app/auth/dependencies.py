"""Authentication & authorization dependencies for the API layer.

* :func:`get_current_user` — resolves and validates the bearer access token, loads the user,
  enforces account state and token-version, and binds the user id to the request/logging
  context.
* :func:`require_permissions` / :func:`require_role` — dependency factories that gate a route
  on the caller's RBAC grants.

These are the single enforcement point for authenticated access; later phases reuse them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Permission, Role, permissions_for, role_has_permission
from app.auth.tokens import TokenType, decode_token
from app.core.config import Settings, get_settings
from app.core.context import user_id_ctx
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.db.session import get_session
from app.models.user import User
from app.services.user_service import UserService

# auto_error=False so we can raise our own typed AuthenticationError envelope.
_bearer = HTTPBearer(auto_error=False)


def get_user_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserService:
    return UserService(session)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    service: Annotated[UserService, Depends(get_user_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token.")

    claims = decode_token(
        settings.security, credentials.credentials, expected_type=TokenType.ACCESS
    )

    try:
        import uuid

        user_id = uuid.UUID(claims.subject)
    except ValueError as exc:
        raise AuthenticationError("Invalid token subject.") from exc

    user = await service.get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is inactive.")
    if user.token_version != claims.token_version:
        raise AuthenticationError("Token has been revoked.", code="TOKEN_REVOKED")

    # Bind identity for logging/audit correlation for the remainder of the request.
    user_id_ctx.set(str(user.id))
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permissions(*required: Permission):
    """Dependency factory: require the caller to hold *all* given permissions."""

    async def _dependency(user: CurrentUser) -> User:
        granted = permissions_for(Role(user.role))
        missing = [p for p in required if p not in granted]
        if missing:
            raise AuthorizationError(
                "Missing required permission(s).",
                details={"required": [p.value for p in missing]},
            )
        return user

    return _dependency


def require_role(*roles: Role):
    """Dependency factory: require the caller's role to be one of ``roles``."""

    async def _dependency(user: CurrentUser) -> User:
        if Role(user.role) not in roles:
            raise AuthorizationError(
                "Your role does not permit this action.",
                details={"allowed_roles": [r.value for r in roles]},
            )
        return user

    return _dependency


def has_permission(user: User, permission: Permission) -> bool:
    return role_has_permission(Role(user.role), permission)
