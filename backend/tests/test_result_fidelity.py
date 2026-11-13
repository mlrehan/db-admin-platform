"""Result values reach the client faithfully: NULL ≠ '' ≠ 0 ≠ false, big integers exact,
non-finite floats valid JSON — plus per-column value kinds for the results grid."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from app.utils.serialization import JS_SAFE_INTEGER, infer_column_kinds, row_to_list, to_jsonable


def test_null_empty_string_zero_and_false_stay_distinct() -> None:
    assert row_to_list([None, "", 0, 0.0, False, "0"]) == [None, "", 0, 0.0, False, "0"]
    # …and survive a JSON round trip with their types intact.
    assert json.loads(json.dumps(row_to_list([None, "", 0, False]))) == [None, "", 0, False]


def test_integers_beyond_javascript_precision_are_sent_as_exact_strings() -> None:
    assert to_jsonable(JS_SAFE_INTEGER) == JS_SAFE_INTEGER
    assert to_jsonable(-JS_SAFE_INTEGER) == -JS_SAFE_INTEGER
    assert to_jsonable(9007199254740993) == "9007199254740993"  # 2**53 + 1
    assert to_jsonable(-(2**63)) == str(-(2**63))


def test_non_finite_floats_produce_valid_json() -> None:
    values = row_to_list([math.nan, math.inf, -math.inf, 1.5])
    assert values == ["NaN", "Infinity", "-Infinity", 1.5]
    json.dumps(values, allow_nan=False)  # what the API's JSON encoder requires


def test_column_kinds_inferred_from_driver_values() -> None:
    rows = [
        (None, 1, decimal.Decimal("48000.00"), dt.date(2020, 1, 1), dt.datetime(2020, 1, 1, 9), True,
         "x", b"\x00\x01", uuid.uuid4(), {"a": 1}, 1),
        (None, 2, decimal.Decimal("1.5"), None, None, False, "", None, None, None, 2.5),
    ]
    assert infer_column_kinds(rows, 11) == [
        None, "integer", "decimal", "date", "datetime", "boolean", "text", "binary", "uuid", "json",
        "decimal",  # int + float in one column (SQLite dynamic typing) → numeric
    ]
    assert infer_column_kinds([(1,), ("a",)], 1) == ["text"]  # any other mix → text


@pytest.fixture
def sqlite_dir(tmp_path, monkeypatch) -> Iterator[None]:
    from app.core.config import get_settings
    from app.db.adapters import registry
    from app.db.adapters.sqlite import SQLiteAdapter
    from app.db.engines import EngineType

    monkeypatch.setenv("CONN_SQLITE_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    # ASGITransport skips the lifespan that registers built-in adapters: register the real one.
    registry.register_adapter(EngineType.SQLITE, SQLiteAdapter)
    yield
    registry._registry.pop(EngineType.SQLITE, None)  # noqa: SLF001
    get_settings.cache_clear()


async def test_real_sqlite_result_preserves_null_empty_and_falsy_values(
    sqlite_dir, client: AsyncClient, seed_users
) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "admin-password-123"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    conn = await client.post("/api/v1/connections", headers=headers, json={"name": "lite", "engine": "sqlite"})
    assert conn.status_code == 201, conn.text
    sid = (await client.post("/api/v1/sessions", headers=headers,
                             json={"connection_id": conn.json()["id"]})).json()["id"]
    assert (await client.post(f"/api/v1/sessions/{sid}/databases", headers=headers,
                              json={"name": "fidelity"})).status_code == 201
    await client.post(f"/api/v1/sessions/{sid}/database", headers=headers, json={"database": "fidelity.db"})
    setup = await client.post(f"/api/v1/sessions/{sid}/script", headers=headers, json={"sql": (
        "CREATE TABLE t (id INTEGER, middle TEXT, manager INTEGER, flag INTEGER, salary NUMERIC, big INTEGER);\n"
        "INSERT INTO t VALUES (1, NULL, NULL, 0, 48000.5, 9007199254740993), (2, '', 0, 1, 0, 5);"
    )})
    assert setup.json()["success"], setup.text

    # Single-query endpoint (Data Viewer) …
    q = (await client.post(f"/api/v1/sessions/{sid}/query", headers=headers,
                           json={"sql": "SELECT id, middle, manager, flag, salary, big FROM t ORDER BY id"})).json()
    assert q["rows"] == [[1, None, None, 0, 48000.5, "9007199254740993"], [2, "", 0, 1, 0, 5]]
    assert [c["kind"] for c in q["columns"]] == ["integer", "text", "integer", "integer", "decimal", "integer"]

    # … and the script endpoint (SQL Editor) carry the same values and kinds.
    s = (await client.post(f"/api/v1/sessions/{sid}/script", headers=headers,
                           json={"sql": "SELECT middle, manager FROM t ORDER BY id;"})).json()
    rs = next(st for st in s["statements"] if st["returns_rows"])
    assert rs["rows"] == [[None, None], ["", 0]]
    assert [c["kind"] for c in rs["columns"]] == ["text", "integer"]
