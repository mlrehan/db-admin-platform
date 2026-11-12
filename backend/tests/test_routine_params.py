"""IntelliSense parameter hints for user-defined routines: catalog parameter extraction."""

from __future__ import annotations

from typing import Any

from app.db.adapters.mssql import MSSQLAdapter
from app.db.adapters.mysql import MySQLAdapter
from app.db.adapters.sqlalchemy_adapter import SQLAlchemyAdapter, split_argument_list


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[tuple[Any, ...]] | Exception) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _Rows:
        if isinstance(self._rows, Exception):
            raise self._rows
        return _Rows(self._rows)


def test_split_argument_list_respects_parentheses_and_quotes() -> None:
    assert split_argument_list("a integer, b numeric(10,2) DEFAULT 0, c text DEFAULT 'x,y'::text") == (
        "a integer", "b numeric(10,2) DEFAULT 0", "c text DEFAULT 'x,y'::text",
    )
    assert split_argument_list("") == ()
    assert split_argument_list(None) == ()


async def test_mssql_parameters_output_and_function_return_type() -> None:
    rows = [
        # schema, routine, ordinal, mode, name, type, char_len, precision, scale
        ("dbo", "usp_Orders", 1, "IN", "@CustomerId", "int", None, 10, 0),
        ("dbo", "usp_Orders", 2, "IN", "@Name", "nvarchar", 50, None, None),
        ("dbo", "usp_Orders", 3, "INOUT", "@Total", "decimal", None, 12, 2),
        ("dbo", "fn_Tax", 0, "OUT", "", "money", None, 19, 4),  # return value
        ("dbo", "fn_Tax", 1, "IN", "@Amount", "money", None, 19, 4),
    ]
    adapter = MSSQLAdapter.__new__(MSSQLAdapter)
    result = await SQLAlchemyAdapter._routine_params(adapter, _Conn(rows))  # type: ignore[arg-type]
    assert result[("dbo", "usp_orders")] == (
        ["@CustomerId int", "@Name nvarchar(50)", "@Total decimal(12,2) OUTPUT"], None,
    )
    assert result[("dbo", "fn_tax")] == (["@Amount money"], "money")


async def test_mysql_out_parameters_are_labelled() -> None:
    rows = [("shop", "sp_total", 1, "IN", "p_id", "int", None, None, None),
            ("shop", "sp_total", 2, "OUT", "p_total", "decimal(10,2)", None, None, None)]
    adapter = MySQLAdapter.__new__(MySQLAdapter)
    result = await SQLAlchemyAdapter._routine_params(adapter, _Conn(rows))  # type: ignore[arg-type]
    assert result[("shop", "sp_total")] == (["p_id int", "OUT p_total decimal(10,2)"], None)


async def test_parameter_query_failure_degrades_to_names_only() -> None:
    adapter = MSSQLAdapter.__new__(MSSQLAdapter)
    assert await SQLAlchemyAdapter._routine_params(adapter, _Conn(PermissionError("denied"))) == {}  # type: ignore[arg-type]
    routine = adapter._catalog_routine(("dbo", "usp_X", "PROCEDURE"), {})
    assert routine.params is None  # unknown — the editor shows the name only


def test_routine_without_parameters_is_marked_empty_when_params_are_known() -> None:
    adapter = MSSQLAdapter.__new__(MSSQLAdapter)
    known = {("dbo", "usp_other"): (["@a int"], None)}
    assert adapter._catalog_routine(("dbo", "usp_NoArgs", "PROCEDURE"), known).params == ()


def test_postgres_inline_arguments() -> None:
    adapter = MSSQLAdapter.__new__(MSSQLAdapter)
    r = adapter._catalog_routine(("public", "add_days", "FUNCTION", "d date, n integer DEFAULT 1", "date"), {})
    assert r.params == ("d date", "n integer DEFAULT 1") and r.returns == "date"
