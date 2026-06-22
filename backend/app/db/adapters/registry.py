"""Adapter registry / factory.

Concrete adapters register a factory for their :class:`~app.db.engines.EngineType` at import
time (Phase 4). The orchestrator resolves an adapter purely by engine, so adding a new engine
is a matter of registering it here — no orchestrator changes. Until Phase 4 registers the
built-in engines, :func:`create_adapter` raises :class:`UnsupportedEngineError` for every
engine, which is the correct, honest behaviour (not a stub).
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.exceptions import UnsupportedEngineError
from app.db.adapters.base import ConnectionConfig, DatabaseAdapter
from app.db.engines import EngineType

AdapterFactory = Callable[[ConnectionConfig], DatabaseAdapter]

_registry: dict[EngineType, AdapterFactory] = {}


def register_adapter(engine: EngineType, factory: AdapterFactory) -> None:
    _registry[engine] = factory


def is_registered(engine: EngineType) -> bool:
    return engine in _registry


def supported_engines() -> frozenset[EngineType]:
    return frozenset(_registry)


def create_adapter(config: ConnectionConfig) -> DatabaseAdapter:
    factory = _registry.get(config.engine)
    if factory is None:
        raise UnsupportedEngineError(
            f"No adapter registered for engine '{config.engine.value}'.",
            details={"engine": config.engine.value},
        )
    return factory(config)
