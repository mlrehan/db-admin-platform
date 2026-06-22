"""Structured logging configuration.

Two formats are supported, selected by ``LOG_FORMAT``:

* ``json``    — one JSON object per line, suitable for log shippers / production.
* ``console`` — compact human-readable lines for local development.

Every record is enriched with the request id from :mod:`app.core.context` so log lines can
be correlated across a request's lifecycle. Configuration is applied exactly once via
:func:`configure_logging`, which also routes uvicorn's loggers through the same handler.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.config import LoggingSettings
from app.core.context import get_request_id, get_user_id

_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime", "taskName"}


class ContextFilter(logging.Filter):
    """Attach request-scoped identifiers to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.user_id = get_user_id()
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if request_id := getattr(record, "request_id", None):
            payload["request_id"] = request_id
        if user_id := getattr(record, "user_id", None):
            payload["user_id"] = user_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Promote any structured `extra=...` fields the caller attached.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    _FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(rid)s%(message)s"

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None)
        record.rid = f"[{rid}] " if rid else ""
        formatter = logging.Formatter(self._FMT, datefmt="%H:%M:%S")
        return formatter.format(record)


def configure_logging(settings: LoggingSettings) -> None:
    """Idempotently configure the root logger and align uvicorn's loggers."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JSONFormatter() if settings.format == "json" else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.level)

    # Route uvicorn through our handler; disable its default duplicate handlers.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy engine logging is governed by `echo`, keep its own logger quiet otherwise.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
