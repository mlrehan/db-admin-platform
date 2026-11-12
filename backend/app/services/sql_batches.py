"""Client-side SQL script structure: batches and statements.

Two separators used by database client tools are **directives to the client**, never SQL sent
to the server:

* ``GO`` (SQL Server Management Studio / sqlcmd) ends a *batch*. Some T-SQL statements must be
  the first in their batch (``CREATE PROCEDURE/VIEW/TRIGGER/FUNCTION``), and local variables
  live only within one batch, so scripts rely on it. ``GO n`` executes the preceding batch
  ``n`` times.
* ``DELIMITER xx`` (mysql client) changes the statement terminator so a routine body that
  contains ``;`` can be sent as one statement.

This module splits a script exactly as those tools do — a separator is only recognised on its
own line and never inside a string literal, a quoted identifier or a (possibly nested) comment
— and splits statements while keeping procedural ``BEGIN … END`` bodies of
``CREATE PROCEDURE/FUNCTION/TRIGGER/EVENT`` intact (MySQL, SQLite, PostgreSQL ``BEGIN ATOMIC``).

It is used both by the execution engine and by the access-control analyzer, so permission
checks always see exactly the statements that will run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.engines import EngineType

# A single GO may repeat its batch; cap it so a script can't trigger unbounded execution.
MAX_GO_REPEAT = 1000

_GO_LINE_RE = re.compile(r"^[ \t]*go(?:[ \t]+(\d+))?[ \t]*(?:--[^\n]*)?$", re.IGNORECASE)
_DELIMITER_LINE_RE = re.compile(r"^[ \t]*delimiter[ \t]+(\S+)[ \t]*$", re.IGNORECASE)
_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
# CREATE [OR REPLACE] [DEFINER=…] [TEMP] {PROCEDURE|FUNCTION|TRIGGER|EVENT} — bodies may hold ';'.
# Used with ``pattern.match(text, pos)``, which anchors at ``pos`` — so NO leading ``^`` (in
# Python ``^`` only matches at the real start of the string, which would miss every statement
# after the first one).
_COMPOUND_HEAD_RE = re.compile(
    r"\s*create\s+(?:or\s+replace\s+)?(?:or\s+alter\s+)?"
    r"(?:definer\s*=\s*(?:`[^`]*`|'[^']*'|\S+?)(?:@(?:`[^`]*`|'[^']*'|\S+))?\s+)?"
    r"(?:temp(?:orary)?\s+)?(?:procedure|function|trigger|event)\b",
    re.IGNORECASE,
)
# "END IF / END LOOP / …" close a construct whose opener we don't count, so they don't change depth.
_END_QUALIFIERS = {"if", "loop", "while", "repeat", "for"}


class BatchParseError(ValueError):
    """A script's batch structure is invalid (e.g. ``GO 0`` or an excessive repeat count)."""


@dataclass(frozen=True)
class SqlBatch:
    """One client-side batch: the SQL sent to the server in a single round trip."""

    index: int  # 1-based position in the script
    sql: str
    start_line: int  # 1-based script line where the batch starts (for error reporting)
    repeat: int = 1  # from ``GO n``


# --- GO batch separation ----------------------------------------------------------------


def split_go_batches(script: str, *, max_repeat: int = MAX_GO_REPEAT) -> list[SqlBatch]:
    """Split a T-SQL script into batches on ``GO`` lines (SSMS/sqlcmd semantics).

    ``GO`` is recognised only when it is alone on its line (optionally followed by a repeat
    count and/or a ``--`` comment) and the line does not start inside a string literal, a
    quoted identifier (``"…"`` / ``[…]``) or a block comment (T-SQL block comments nest).
    Whitespace-only batches are dropped. Raises :class:`BatchParseError` for ``GO 0`` or a
    repeat count above ``max_repeat``."""
    return _scan_go(script, max_repeat)[0]


def has_go_separator(script: str) -> bool:
    """Whether the script contains at least one real ``GO`` separator line."""
    if not re.search(r"(?im)^[ \t]*go\b", script):  # fast path
        return False
    try:
        return _scan_go(script, MAX_GO_REPEAT)[1]
    except BatchParseError:
        return True


def _scan_go(script: str, max_repeat: int) -> tuple[list[SqlBatch], bool]:
    batches: list[SqlBatch] = []
    seen_go = False
    n = len(script)
    i = 0
    line = 1
    batch_start = 0
    batch_line = 1
    block_depth = 0
    quote: str | None = None  # closing char of the literal/identifier we're inside
    at_line_start = True

    def emit(end: int, repeat: int) -> None:
        text = script[batch_start:end]
        if text.strip():
            batches.append(
                SqlBatch(index=len(batches) + 1, sql=text.strip("\r\n"), start_line=batch_line,
                         repeat=repeat)
            )

    while i < n:
        if at_line_start and quote is None and block_depth == 0:
            eol = script.find("\n", i)
            end = n if eol == -1 else eol
            m = _GO_LINE_RE.match(script[i:end].rstrip("\r"))
            if m:
                seen_go = True
                repeat = int(m.group(1)) if m.group(1) else 1
                if repeat < 1:
                    raise BatchParseError(f"Line {line}: 'GO {repeat}' - the count must be at least 1.")
                if repeat > max_repeat:
                    raise BatchParseError(
                        f"Line {line}: 'GO {repeat}' exceeds the maximum repeat count ({max_repeat})."
                    )
                emit(i, repeat)
                i = n if eol == -1 else eol + 1
                line += 1
                batch_start = i
                batch_line = line
                continue
        at_line_start = False
        ch = script[i]
        if ch == "\n":
            line += 1
            at_line_start = True
            i += 1
            continue
        if quote is not None:
            if ch == quote:
                if i + 1 < n and script[i + 1] == quote:  # doubled = escaped
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if block_depth:
            if script.startswith("/*", i):
                block_depth += 1
                i += 2
            elif script.startswith("*/", i):
                block_depth -= 1
                i += 2
            else:
                i += 1
            continue
        if script.startswith("--", i):
            eol = script.find("\n", i)
            i = n if eol == -1 else eol  # the newline itself is handled above
            continue
        if script.startswith("/*", i):
            block_depth = 1
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "[":
            quote = "]"
        i += 1
    emit(n, 1)
    return batches, seen_go


def strip_go_lines(script: str) -> str:
    """Return the script with every ``GO`` separator replaced by a statement break (used for
    access analysis on any engine, so a statement glued after a ``GO`` line is never hidden)."""
    try:
        return ";\n".join(b.sql for b in split_go_batches(script))
    except BatchParseError:
        return script


# --- statement splitting ----------------------------------------------------------------


def split_statements(script: str, engine: EngineType | None = None) -> list[str]:
    """Split a script into individual statements, preserving each statement's original text.

    Dialect-aware: ``#`` line comments and backslash string escapes for MySQL; dollar-quoted
    bodies and ``E'…'`` escapes for PostgreSQL; ``[…]`` identifiers and nested block comments
    for SQL Server. The bodies of ``CREATE PROCEDURE/FUNCTION/TRIGGER/EVENT … BEGIN … END`` are
    kept whole (they contain ``;``). For MySQL a ``DELIMITER`` directive changes the terminator,
    exactly like the mysql client."""
    is_mysql = engine == EngineType.MYSQL
    is_pg = engine == EngineType.POSTGRESQL
    is_mssql = engine == EngineType.MSSQL

    statements: list[str] = []
    n = len(script)
    i = 0
    start = 0
    delimiter = ";"
    at_line_start = True
    # Compound-statement tracking (per statement).
    head_checked = False
    compound = False
    depth = 0

    def push(end: int) -> None:
        chunk = script[start:end].strip()
        if chunk:
            statements.append(chunk)

    while i < n:
        # mysql-client DELIMITER directive (only at a line start, outside literals).
        if is_mysql and at_line_start:
            eol = script.find("\n", i)
            end = n if eol == -1 else eol
            m = _DELIMITER_LINE_RE.match(script[i:end].rstrip("\r"))
            if m:
                push(i)
                delimiter = m.group(1)
                i = n if eol == -1 else eol + 1
                start = i
                head_checked = compound = False
                depth = 0
                continue
        at_line_start = False
        ch = script[i]

        if ch == "\n":
            at_line_start = True
            i += 1
            continue
        # Comments.
        if script.startswith("--", i) or (is_mysql and ch == "#"):
            eol = script.find("\n", i)
            i = n if eol == -1 else eol
            continue
        if script.startswith("/*", i):
            i = _skip_block_comment(script, i, nested=is_mssql)
            continue
        # String literals / quoted identifiers.
        if ch == "'":
            backslash = is_mysql or (is_pg and i > 0 and script[i - 1] in "eE" and _is_word_start(script, i - 1))
            i = _skip_quoted(script, i, "'", backslash)
            continue
        if ch == '"':
            i = _skip_quoted(script, i, '"', is_mysql)
            continue
        if ch == "`":
            i = _skip_quoted(script, i, "`", False)
            continue
        if is_mssql and ch == "[":
            i = _skip_quoted(script, i, "]", False, open_char="[")
            continue
        if ch == "$" and (is_pg or engine is None):
            m = _DOLLAR_TAG_RE.match(script, i)
            if m:
                tag = m.group(0)
                close = script.find(tag, m.end())
                i = n if close == -1 else close + len(tag)
                continue

        # Statement terminator.
        if script.startswith(delimiter, i) and (delimiter != ";" or depth == 0):
            push(i)
            i += len(delimiter)
            start = i
            head_checked = compound = False
            depth = 0
            continue

        # Words: detect a compound CREATE header, then track BEGIN/CASE … END nesting.
        if (ch.isalpha() or ch == "_") and _is_word_start(script, i):
            m = _WORD_RE.match(script, i)
            word = m.group(0).lower()
            if not head_checked:
                head_checked = True
                compound = bool(_COMPOUND_HEAD_RE.match(script, start)) if word == "create" else False
            if compound:
                if word in ("begin", "case"):
                    depth += 1
                elif word == "end":
                    nxt = _WORD_RE.match(script, _skip_ws(script, m.end()))
                    if not (nxt and nxt.group(0).lower() in _END_QUALIFIERS):
                        depth = max(0, depth - 1)
            i = m.end()
            continue
        i += 1
    push(n)
    return statements


def _is_word_start(s: str, i: int) -> bool:
    return i == 0 or not (s[i - 1].isalnum() or s[i - 1] in "_$@#")


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _skip_quoted(s: str, i: int, close: str, backslash: bool, open_char: str | None = None) -> int:
    """Return the index just past a quoted literal/identifier starting at ``i``. A doubled
    closing char is an escape; with ``backslash`` a backslash escapes the next char."""
    n = len(s)
    j = i + 1
    while j < n:
        c = s[j]
        if backslash and c == "\\":
            j += 2
            continue
        if c == close:
            if j + 1 < n and s[j + 1] == close:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def _skip_block_comment(s: str, i: int, *, nested: bool) -> int:
    n = len(s)
    depth = 1
    j = i + 2
    while j < n:
        if nested and s.startswith("/*", j):
            depth += 1
            j += 2
            continue
        if s.startswith("*/", j):
            depth -= 1
            j += 2
            if depth == 0:
                return j
            continue
        j += 1
    return n
