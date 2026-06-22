"""User administration endpoints (Admin only).

CRUD over platform principals, gated by the ``user:manage`` permission (Admin role). The
SQL/connection-facing admin panel UI consumes these in Phase 8; the endpoints themselves are
part of the authentication subsystem (Phase 2).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.auth.dependencies import get_user_service, require_permissions
from app.auth.roles import Permission
from app.core.exceptions import ConflictError, NotFoundError
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services.user_service import UserService

router = APIRouter(tags=["users"])

# Every route in this router requires platform user-management permission.
_admin_only = Depends(require_permissions(Permission.USER_MANAGE))


@router.get("", response_model=list[UserRead], dependencies=[_admin_only])
async def list_users(
    service: Annotated[UserService, Depends(get_user_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[User]:
    return await service.list_users(limit=limit, offset=offset)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_only],
)
async def create_user(
    payload: UserCreate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    return await service.create_user(
        email=payload.email,
        password=payload.password,
        role=payload.role,
        full_name=payload.full_name,
    )


@router.get("/{user_id}", response_model=UserRead, dependencies=[_admin_only])
async def get_user(
    user_id: uuid.UUID,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    user = await service.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return user


@router.patch("/{user_id}", response_model=UserRead, dependencies=[_admin_only])
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    user = await service.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return await service.update_profile(
        user,
        full_name=payload.full_name,
        role=payload.role,
        is_active=payload.is_active,
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[_admin_only],
)
async def delete_user(
    user_id: uuid.UUID,
    actor: Annotated[User, Depends(require_permissions(Permission.USER_MANAGE))],
    service: Annotated[UserService, Depends(get_user_service)],
) -> None:
    if actor.id == user_id:
        raise ConflictError("You cannot delete your own account.")
    await service.delete_user(user_id)
