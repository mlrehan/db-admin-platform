"""JSON-safe conversion of database values.

Query results contain driver-native types (``datetime``, ``Decimal``, ``UUID``, ``bytes``,
``memoryview``, etc.) that are not directly JSON-serializable. :func:`to_jsonable` converts a
single value; :func:`row_to_list` converts a whole row. The goal is a faithful, lossless-ish
representation the frontend (AG Grid) can render:

* ``Decimal`` → ``str`` (avoids float precision loss)
* ``datetime``/``date``/``time`` → ISO-8601 string
* ``bytes``/``memoryview`` → base64 string
* ``UUID`` → ``str``
* containers → recursively converted
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import ipaddress
import uuid
from collections.abc import Mapping, Sequence
from typing import Any


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
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
