"""SQL Server batch execution + cancellation, against a stub ODBC driver (no server needed)."""

from __future__ import annotations

from app.db.adapters.base import ConnectionConfig
from app.db.adapters.mssql import MSSQLAdapter
from app.db.engines import EngineType


class _StubCursor:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.description = None
        self.rowcount = -1

    async def execute(self, sql: str) -> None:
        self._log.append(sql)

    async def nextset(self) -> bool:
        return False

    async def close(self) -> None:
        pass


class _StubDriver:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def cursor(self) -> _StubCursor:
        return _StubCursor(self.sent)


class _StubConn:
    invalidated = False
    closed = False

    def __init__(self, driver: _StubDriver) -> None:
        self._raw = type("Raw", (), {"driver_connection": driver})()

    async def get_raw_connection(self):
        return self._raw


def _adapter() -> MSSQLAdapter:
    return MSSQLAdapter(
        ConnectionConfig(engine=EngineType.MSSQL, host="h", port=1433, database="db",
                         username="u", password="p")
    )


async def _run(adapter: MSSQLAdapter, sql: str) -> tuple[_StubDriver, list[str], list[str]]:
    driver = _StubDriver()
    messages: list[str] = []
    errors: list[str] = []
    await adapter._run_script_batch(_StubConn(driver), sql, 100, [], messages, errors)
    return driver, messages, errors


async def test_each_go_batch_is_sent_separately_and_go_never_reaches_the_server() -> None:
    driver, messages, errors = await _run(_adapter(), "SELECT 1\nGO\nSELECT 2\nGO 3\n")
    assert driver.sent == ["SELECT 1", "SELECT 2", "SELECT 2", "SELECT 2"]
    assert not errors
    assert "2 batch(es) completed successfully." in messages


async def test_cancel_before_any_batch_starts_runs_nothing() -> None:
    # Regression: a cancel that arrives while the session connection is still opening (before
    # the first batch) must stop the script — previously the batch loop only checked the flag
    # AFTER a batch finished, so the first (possibly very long) batch still ran.
    adapter = _adapter()
    adapter._cancel_requested = True
    driver, _, errors = await _run(adapter, "WAITFOR DELAY '00:00:20'\nGO\nSELECT 2\nGO 5")
    assert driver.sent == []
    assert errors == ["Query was cancelled by the user."]


async def test_go_parse_error_sends_nothing() -> None:
    driver, _, errors = await _run(_adapter(), "SELECT 1\nGO 0")
    assert driver.sent == [] and "GO 0" in errors[0]
