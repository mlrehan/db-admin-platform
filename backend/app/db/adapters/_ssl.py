"""Shared SSL/TLS context construction for adapters.

Maps a libpq-style ``ssl_mode`` string onto a Python :class:`ssl.SSLContext`:

* ``disable`` / ``allow`` / ``prefer`` / ``None`` → no TLS (returns ``None``)
* ``require``                                    → encrypt, but do **not** verify the cert
* ``verify-ca``                                  → verify the CA chain (not the hostname)
* ``verify-full``                                → verify CA chain **and** hostname

Adapters whose drivers accept an ``SSLContext`` (asyncpg, aiomysql) reuse this; MSSQL/ODBC
expresses TLS via DSN keywords instead.
"""

from __future__ import annotations

import ssl

_NO_TLS = frozenset({None, "", "disable", "allow", "prefer"})


def build_ssl_context(ssl_mode: str | None) -> ssl.SSLContext | None:
    if ssl_mode in _NO_TLS:
        return None
    context = ssl.create_default_context()
    if ssl_mode == "require":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif ssl_mode == "verify-ca":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
    elif ssl_mode == "verify-full":
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
    else:
        # Unknown mode: be safe and require a verified certificate.
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
    return context
