"""Shared server-side metadata cache.

Schema metadata (databases, schemas, tables, columns, routines, the IntelliSense catalog) is
identical for every session opened on the same saved connection + database, and it is read far
more often than it changes. Without a cache, 100 users opening the Schema Explorer or typing in
the editor would each run the same catalog queries against the target database.

* **Scope** — entries are keyed by ``(connection_id, database)``, so all sessions on one
  connection share them (they use the same credentials, so the database returns the same
  metadata). Per-user access filtering is applied *after* the cache, at the API layer.
* **Single-flight** — concurrent misses for the same key share ONE database round trip.
* **Freshness** — a TTL bounds staleness for changes made outside the platform, and any DDL run
  through the platform invalidates the connection's entries immediately — and those of every
  OTHER saved connection pointing at the same server (two connections to one SQL Server, or all
  SQLite connections sharing the data directory), so a database created through one connection
  shows up at once in the others. A generation counter prevents a load that raced with an
  invalidation from re-inserting stale data.
* **Bounded** — LRU eviction caps memory.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

Key = tuple[Any, ...]


class MetadataCache:
    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 20_000) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: OrderedDict[Key, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[Key, asyncio.Future[Any]] = {}
        self._generation: dict[Any, int] = {}
        # scope (connection id) -> server identity, so DDL through one connection also
        # invalidates other connections to the same server. Bounded by the number of saved
        # connections.
        self._server_of: dict[Any, Any] = {}
        self.hits = 0
        self.misses = 0

    def configure(self, *, ttl_seconds: float | None = None, max_entries: int | None = None) -> None:
        if ttl_seconds is not None:
            self._ttl = ttl_seconds
        if max_entries is not None:
            self._max = max_entries

    async def get_or_load(self, key: Key, loader: Callable[[], Awaitable[T]]) -> T:
        """Return the cached value for ``key`` or load it once (shared by concurrent callers).

        ``key[0]`` must be the scope used for invalidation (the connection id)."""
        entry = self._data.get(key)
        if entry is not None and time.monotonic() - entry[0] < self._ttl:
            self._data.move_to_end(key)
            self.hits += 1
            return entry[1]
        pending = self._inflight.get(key)
        if pending is not None:
            self.hits += 1
            return await asyncio.shield(pending)

        self.misses += 1
        scope = key[0]
        generation = self._generation.get(scope, 0)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        # Retrieve the exception if nobody awaited it (avoids "never retrieved" warnings).
        future.add_done_callback(lambda f: f.cancelled() or f.exception())
        self._inflight[key] = future
        try:
            value = await loader()
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)
        if not future.done():
            future.set_result(value)
        # Don't cache a value that an invalidation (DDL) made stale while it was loading.
        if self._generation.get(scope, 0) == generation:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
        return value

    def link(self, scope: Any, server: Any) -> None:
        """Record which server a scope (connection) points at — see :func:`server_identity`."""
        if server is not None:
            self._server_of[scope] = server

    def invalidate(self, scope: Any, database: str | None = None, *, server: Any = None) -> int:
        """Drop every entry for a connection (optionally one database) AND for every other
        connection known to point at the same server. Returns the number of entries dropped."""
        self.link(scope, server)
        target = self._server_of.get(scope)
        scopes = {scope}
        if target is not None:
            scopes |= {s for s, srv in self._server_of.items() if srv == target}
        return sum(self._invalidate_scope(s, database) for s in scopes)

    def _invalidate_scope(self, scope: Any, database: str | None) -> int:
        self._generation[scope] = self._generation.get(scope, 0) + 1
        doomed = [
            k for k in self._data
            if k[0] == scope and (database is None or len(k) < 2 or k[1] == _db_key(database))
        ]
        for k in doomed:
            self._data.pop(k, None)
        return len(doomed)

    def clear(self) -> None:
        self._data.clear()
        self._generation.clear()
        self._server_of.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses,
                "inflight": len(self._inflight)}


def _db_key(database: str | None) -> str:
    return (database or "").lower()


def server_identity(config: Any) -> tuple[str, str, int]:
    """Identity of the server a connection points at: engine + host + port. Every SQLite
    connection maps to the same identity (they share the server's data directory)."""
    engine = getattr(config.engine, "value", config.engine)
    return (str(engine), (config.host or "").strip().lower(), int(config.port or 0))


def scope_key(connection_id: uuid.UUID, database: str | None) -> tuple[uuid.UUID, str]:
    return (connection_id, _db_key(database))


# One process-wide cache (the API runs as a single async worker).
metadata_cache = MetadataCache()
