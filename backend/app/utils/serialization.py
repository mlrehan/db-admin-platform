"""JSON-safe conversion of database values, and column *kind* inference for result grids.

Query results contain driver-native types (``datetime``, ``Decimal``, ``UUID``, ``bytes``,
``memoryview``, etc.) that are not directly JSON-serializable. :func:`to_jsonable` converts a
single value; :func:`row_to_list` converts a whole row. The goal is a faithful, lossless
representation the frontend grid can render:

* SQL ``NULL`` (``None``) → JSON ``null`` — never collapsed into ``""``, ``0`` or ``false``, so
  the UI can show NULL distinctly from an empty string
* ``Decimal`` → ``str`` (avoids float precision loss)
* integers outside JavaScript's exact range (±2^53-1, e.g. bigint keys) → ``str`` — as a JSON
  number the browser would silently round them (9007199254740993 → …992)
* non-finite floats (NaN, ±Infinity) → ``"NaN"``/``"Infinity"``/``"-Infinity"`` — as bare tokens
  they are invalid JSON and would fail the whole response
* ``datetime``/``date``/``time`` → ISO-8601 string
* ``bytes``/``memoryview`` → base64 string
* ``UUID`` → ``str``
* containers → recursively converted

Because several kinds travel as strings (decimals, big integers, dates), the original Python
type is lost after serialization. :func:`infer_column_kinds` therefore classifies every column
from the *driver* values before conversion — portable across all engines, with no extra
round trip — so the grid can right-align and numerically sort numbers, format dates, etc.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import ipaddress
import math
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

# Largest integer a JavaScript Number represents exactly (Number.MAX_SAFE_INTEGER).
JS_SAFE_INTEGER = 2**53 - 1


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value if -JS_SAFE_INTEGER <= value <= JS_SAFE_INTEGER else str(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    ):
        return [to_jsonable(v) for v in value]
    # Fallback: stringify anything exotic rather than failing the whole response.
    return str(value)


def row_to_list(row: Sequence[Any]) -> list[Any]:
    return [to_jsonable(v) for v in row]


# --- column kinds -------------------------------------------------------------------------------

_NUMERIC_KINDS = frozenset({"integer", "decimal", "float"})
# Rows inspected per column; enough to classify, cheap even for a 100k-row result.
_KIND_SAMPLE_ROWS = 1000


def value_kind(value: Any) -> str | None:
    """Kind of one driver value (``None`` for SQL NULL — it says nothing about the column)."""
    if value is None:
        return None
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, decimal.Decimal):
        return "decimal"
    if isinstance(value, dt.datetime):  # before date: datetime is a date subclass
        return "datetime"
    if isinstance(value, dt.date):
        return "date"
    if isinstance(value, dt.time):
        return "time"
    if isinstance(value, dt.timedelta):
        return "interval"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "binary"
    if isinstance(value, uuid.UUID):
        return "uuid"
    if isinstance(value, (Mapping, list, tuple)):
        return "json"
    if isinstance(value, str):
        return "text"
    return "other"


def infer_column_kinds(rows: Sequence[Sequence[Any]], column_count: int) -> list[str | None]:
    """Classify each column from its non-NULL driver values (first rows only).

    ``None`` means every sampled value was NULL. Mixed numeric kinds (SQLite's dynamic typing:
    ints and reals in one column) are ``"decimal"``; any other mix is ``"text"``.
    """
    seen: list[set[str]] = [set() for _ in range(column_count)]
    for row in rows[:_KIND_SAMPLE_ROWS]:
        for i in range(min(column_count, len(row))):
            kind = value_kind(row[i])
            if kind is not None:
                seen[i].add(kind)
    kinds: list[str | None] = []
    for found in seen:
        if not found:
            kinds.append(None)
        elif len(found) == 1:
            kinds.append(next(iter(found)))
        elif found <= _NUMERIC_KINDS:
            kinds.append("decimal")
        else:
            kinds.append("text")
    return kinds
