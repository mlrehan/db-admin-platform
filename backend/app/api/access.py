"""Access-grant administration endpoints (granular RBAC).

Admin-only (``user:manage``). Lets an administrator define which databases, tables and SQL
operations a user or role may use on a given connection. Enforcement happens in the query
engine and metadata layer — these endpoints only manage the grant records.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import get_access_service
from app.auth.dependencies import require_permissions
from app.auth.roles import Permission
from app.models.access_grant import AccessGrant
from app.schemas.access import (
    AccessGrantCreate,
    AccessGrantOut,
    AccessGrantUpdate,
    OperationsResponse,
    grantable_operations,
)
from app.services.access_control import AccessControlService

router = APIRouter(tags=["access"])

_admin_only = Depends(require_permissions(Permission.USER_MANAGE))


@router.get("/operations", response_model=OperationsResponse, dependencies=[_admin_only])
async def list_operations() -> OperationsResponse:
    return OperationsResponse(operations=grantable_operations())


@router.get("/grants", response_model=list[AccessGrantOut], dependencies=[_admin_only])
async def list_grants(
    service: Annotated[AccessControlService, Depends(get_access_service)],
    connection_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[AccessGrant]:
    return await service.list_grants(connection_id=connection_id)


@router.post(
    "/grants",
    response_model=AccessGrantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_only],
)
async def create_grant(
    payload: AccessGrantCreate,
    service: Annotated[AccessControlService, Depends(get_access_service)],
) -> AccessGrant:
    return await service.create_grant(
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        connection_id=payload.connection_id,
        operations=payload.operations,
        database=payload.database,
        table_schema=payload.table_schema,
        table_name=payload.table_name,
    )


@router.patch("/grants/{grant_id}", response_model=AccessGrantOut, dependencies=[_admin_only])
async def update_grant(
    grant_id: uuid.UUID,
    payload: AccessGrantUpdate,
    service: Annotated[AccessControlService, Depends(get_access_service)],
) -> AccessGrant:
    return await service.update_grant(
        grant_id,
        operations=payload.operations,
        database=payload.database,
        table_schema=payload.table_schema,
        table_name=payload.table_name,
        clear_scope=True,
    )


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[_admin_only],
)
async def delete_grant(
    grant_id: uuid.UUID,
    service: Annotated[AccessControlService, Depends(get_access_service)],
) -> None:
    await service.delete_grant(grant_id)
