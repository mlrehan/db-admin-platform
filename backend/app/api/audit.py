"""Audit log query endpoints.

Any authenticated user may read **their own** activity; only admins (``audit:read``) may read
across users and see IP addresses. Admins may also delete audit records (a controlled,
authorized path; the PostgreSQL append-only DELETE rule is dropped in migration 0008).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_audit_service
from app.auth.dependencies import CurrentUser, has_permission
from app.auth.roles import Permission
from app.core.exceptions import NotFoundError
from app.models.audit import AuditLog
from app.schemas.audit import AuditDeleteRequest, AuditDeleteResult, AuditLogOut
from app.services.audit_service import AuditService

router = APIRouter(tags=["audit"])


def _can_view_all(user: CurrentUser) -> bool:
    """Whether the caller may see *everyone's* audit records + IPs (admins)."""
    return has_permission(user, Permission.AUDIT_READ)


def _to_out(record: AuditLog, *, include_ip: bool) -> AuditLogOut:
    """Serialize an audit record. IP fields are included only for admins; every other caller
    gets no IP at all (the columns are nulled server-side, not merely hidden in the UI)."""
    out = AuditLogOut.model_validate(record)
    if not include_ip:
        out.ip_address = None
        out.ip_private = None
    return out


@router.get("/logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    user: CurrentUser,
    service: Annotated[AuditService, Depends(get_audit_service)],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    connection_id: Annotated[uuid.UUID | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    destructive: Annotated[bool | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditLogOut]:
    """List audit records. Any authenticated user may read **their own** activity; only
    admins (``audit:read``) may read across users and see IP addresses.

    The own-only restriction is enforced here, server-side: a non-privileged caller's
    ``user_id`` filter is *forced* to their own id, so tampering with the query string cannot
    widen access."""
    privileged = _can_view_all(user)
    effective_user_id = user_id if privileged else user.id
    records = await service.search(
        user_id=effective_user_id,
        connection_id=connection_id,
        category=category,
        success=success,
        destructive=destructive,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [_to_out(r, include_ip=privileged) for r in records]


@router.get("/logs/{audit_id}", response_model=AuditLogOut)
async def get_audit_log(
    audit_id: uuid.UUID,
    user: CurrentUser,
    service: Annotated[AuditService, Depends(get_audit_service)],
) -> AuditLogOut:
    record = await service.get(audit_id)
    # Hide existence of records the caller isn't allowed to see (404, not 403 — no info leak).
    privileged = _can_view_all(user)
    if record is None or (not privileged and record.user_id != user.id):
        raise NotFoundError("Audit record not found.")
    return _to_out(record, include_ip=privileged)


@router.post("/logs/delete", response_model=AuditDeleteResult)
async def delete_audit_logs(
    payload: AuditDeleteRequest,
    user: CurrentUser,
    service: Annotated[AuditService, Depends(get_audit_service)],
) -> AuditDeleteResult:
    """Delete one or more audit records by id. **Admin only** — a non-admin is refused even for
    their own records, since the audit trail is an administrative artifact."""
    if not _can_view_all(user):
        raise NotFoundError("Audit record not found.")
    deleted = await service.delete(payload.ids)
    return AuditDeleteResult(deleted=deleted)
