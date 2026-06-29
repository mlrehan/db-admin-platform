"""Request-scoped context.

A :class:`contextvars.ContextVar` carries the current request id (and, from Phase 2, the
authenticated principal) across async boundaries without threading it through every call.
The logging filter reads from here so every log line can be correlated to a request.
"""

from __future__ import annotations

from contextvars import ContextVar

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
# Populated by the auth dependency in Phase 2; declared now so logging/audit can read it.
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
# Client IP of the current request (best-effort; honours a trusted proxy's forwarded header).
client_ip_ctx: ContextVar[str | None] = ContextVar("client_ip", default=None)


def get_request_id() -> str | None:
    return request_id_ctx.get()


def get_user_id() -> str | None:
    return user_id_ctx.get()


def get_client_ip() -> str | None:
    return client_ip_ctx.get()
