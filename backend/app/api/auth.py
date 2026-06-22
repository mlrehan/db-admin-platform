"""Authentication endpoints: login, refresh, logout, profile, password change."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_orchestrator
from app.auth.dependencies import CurrentUser, get_current_user, get_user_service
from app.auth.tokens import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.user import PasswordChange, UserRead
from app.services.orchestrator import ConnectionOrchestrator
from app.services.user_service import UserService

router = APIRouter(tags=["auth"])


def _issue_pair(settings: Settings, *, subject: str, role: str, tv: int) -> TokenResponse:
    access = create_access_token(
        settings.security, subject=subject, role=role, token_version=tv
    )
    refresh = create_refresh_token(
        settings.security, subject=subject, role=role, token_version=tv
    )
    return TokenResponse(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_in=access.expires_in,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    service: Annotated[UserService, Depends(get_user_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    user = await service.authenticate(email=payload.email, password=payload.password)
    if user is None:
        # Uniform message regardless of which check failed (no user enumeration).
        raise AuthenticationError("Invalid email or password.")
    await service.record_login(user)
    return _issue_pair(
        settings, subject=str(user.id), role=user.role.value, tv=user.token_version
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    service: Annotated[UserService, Depends(get_user_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    claims = decode_token(
        settings.security, payload.refresh_token, expected_type=TokenType.REFRESH
    )
    try:
        user_id = uuid.UUID(claims.subject)
    except ValueError as exc:
        raise AuthenticationError("Invalid token subject.") from exc

    user = await service.get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is inactive.")
    if user.token_version != claims.token_version:
        raise AuthenticationError("Token has been revoked.", code="TOKEN_REVOKED")

    # Rotation: a fresh access+refresh pair is issued on every refresh.
    return _issue_pair(
        settings, subject=str(user.id), role=user.role.value, tv=user.token_version
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def logout(
    user: CurrentUser,
    service: Annotated[UserService, Depends(get_user_service)],
    orchestrator: Annotated[ConnectionOrchestrator, Depends(get_orchestrator)],
) -> None:
    # Close every live database session this user has open (frees server-side connections),
    # then revoke all their tokens (logout-everywhere — stateless JWTs can't be revoked one
    # at a time, so we bump token_version).
    await orchestrator.close_all_for_user(user.id)
    await service.revoke_all_sessions(user)


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def change_password(
    payload: PasswordChange,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UserService, Depends(get_user_service)],
) -> None:
    from app.security import password as pwd

    if not pwd.verify_password(user.hashed_password, payload.current_password):
        raise AuthenticationError("Current password is incorrect.")
    # Revokes existing sessions; client must re-authenticate with the new password.
    await service.change_password(user, new_password=payload.new_password)
