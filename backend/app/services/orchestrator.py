"""Connection Orchestrator — the core engine of the platform.

Owns the lifecycle of **live** connections to target databases. Responsibilities:

* **Lifecycle** — open, track, and close live sessions; reap idle ones via a background task.
* **Isolation** — every live session is a distinct :class:`~app.db.adapters.base.DatabaseAdapter`
  instance bound to exactly one user. Adapters/pools are never shared across users or
  sessions. Cross-user access is impossible: a session is only resolvable by its owner.
* **Pooling per session** — each session holds its own private pool inside its adapter, so
  one user's workload can never starve or observe another's connections.
* **Resource guards** — a per-user cap on concurrent sessions.

The orchestrator depends only on the adapter abstraction and the registry; it has no
knowledge of any specific engine. A single instance is created at app startup and shared
(it is internally concurrency-safe via an :class:`asyncio.Lock`).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.config import ConnectionSettings
from app.core.exceptions import NotFoundError, SessionLimitError
from app.core.logging import get_logger
from app.db.adapters.base import ConnectionConfig, DatabaseAdapter
from app.db.adapters.registry import create_adapter

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class LiveSession:
    """A single live, isolated connection owned by one user."""

    id: uuid.UUID
    user_id: uuid.UUID
    connection_id: uuid.UUID
    adapter: DatabaseAdapter
    created_at: datetime = field(default_factory=_now)
    last_used_at: datetime = field(default_factory=_now)
    # Serializes statement execution over the session's connection (Phase 5 uses this),
    # guaranteeing one in-flight statement per session.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_used_at = _now()

    def idle_seconds(self) -> float:
        return (_now() - self.last_used_at).total_seconds()


class ConnectionOrchestrator:
    def __init__(self, settings: ConnectionSettings) -> None:
        self._settings = settings
        self._sessions: dict[uuid.UUID, LiveSession] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    # --- lifecycle (driven by app lifespan) ----------------------------------------------

    async def start(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop(), name="session-reaper")
            logger.info("Connection orchestrator started")

    async def stop(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            try:
                await self._reaper
            except asyncio.CancelledError:
                pass
            self._reaper = None
        await self.close_all()
        logger.info("Connection orchestrator stopped")

    # --- session management --------------------------------------------------------------

    async def open_session(
        self,
        *,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
        config: ConnectionConfig,
    ) -> LiveSession:
        """Create, connect and register a new isolated live session for ``user_id``.

        Raises :class:`SessionLimitError` if the user is at their concurrent-session cap, and
        propagates adapter connection errors (the partially-built adapter is closed first).
        """
        async with self._lock:
            if self._count_for_user(user_id) >= self._settings.max_sessions_per_user:
                raise SessionLimitError(
                    details={"limit": self._settings.max_sessions_per_user}
                )

        adapter = create_adapter(config)
        try:
            await adapter.connect()
        except Exception:
            await _safe_close(adapter)
            raise

        session = LiveSession(
            id=uuid.uuid4(),
            user_id=user_id,
            connection_id=connection_id,
            adapter=adapter,
        )
        async with self._lock:
            self._sessions[session.id] = session
        logger.info(
            "Live session opened",
            extra={
                "session_id": str(session.id),
                "connection_id": str(connection_id),
                **config.redacted(),
            },
        )
        return session

    async def get_session(self, session_id: uuid.UUID, *, user_id: uuid.UUID) -> LiveSession:
        """Return the caller's session, or 404. Ownership is enforced here — this is the
        single point that makes cross-user session access impossible."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.user_id != user_id:
                raise NotFoundError("Session not found.")
            session.touch()
            return session

    async def list_sessions(self, *, user_id: uuid.UUID) -> list[LiveSession]:
        async with self._lock:
            return [s for s in self._sessions.values() if s.user_id == user_id]

    async def close_session(self, session_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.user_id != user_id:
                raise NotFoundError("Session not found.")
            del self._sessions[session_id]
        await _safe_close(session.adapter)
        logger.info("Live session closed", extra={"session_id": str(session_id)})

    async def close_all_for_user(self, user_id: uuid.UUID) -> int:
        async with self._lock:
            owned = [s for s in self._sessions.values() if s.user_id == user_id]
            for s in owned:
                del self._sessions[s.id]
        for s in owned:
            await _safe_close(s.adapter)
        return len(owned)

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            await _safe_close(s.adapter)

    # --- internals -----------------------------------------------------------------------

    def _count_for_user(self, user_id: uuid.UUID) -> int:
        return sum(1 for s in self._sessions.values() if s.user_id == user_id)

    async def _reap_loop(self) -> None:
        interval = self._settings.reaper_interval_seconds
        ttl = self._settings.session_idle_ttl_seconds
        while True:
            try:
                await asyncio.sleep(interval)
                await self._reap_idle(ttl)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the reaper die on a transient error
                logger.exception("Session reaper iteration failed")

    async def _reap_idle(self, ttl: int) -> None:
        async with self._lock:
            expired = [s for s in self._sessions.values() if s.idle_seconds() > ttl]
            for s in expired:
                del self._sessions[s.id]
        for s in expired:
            await _safe_close(s.adapter)
            logger.info(
                "Reaped idle session",
                extra={"session_id": str(s.id), "idle_seconds": round(s.idle_seconds(), 1)},
            )


async def _safe_close(adapter: DatabaseAdapter) -> None:
    try:
        await adapter.close()
    except Exception:  # closing must never raise into orchestrator control flow
        logger.warning("Error while closing adapter", exc_info=True)
