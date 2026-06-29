"""Script-aware access analysis.

The per-statement analyzer (:func:`extract_access`) is fine for single statements but breaks on
real multi-statement scripts: it treats temporary tables and table variables as if they were
real tables (denying a SELECT the user is allowed to run), and it fails closed on procedural
T-SQL (``DECLARE`` / ``SET`` / ``IF`` / cursors / ``EXEC``).

This module analyzes a whole script the way the database session does:

* **Session-local objects** — SQL Server ``#temp`` / ``@table_var`` / global ``##temp``, and
  ``CREATE TEMP[ORARY] TABLE`` / ``SELECT … INTO #temp`` — are *not* real schema objects, so
  reading/writing/dropping them needs **no grant**.
* **Procedural / control-flow** statements (``DECLARE`` ``SET`` ``IF`` ``ELSE`` ``WHILE``
  ``BEGIN`` ``OPEN`` ``FETCH`` ``CLOSE`` ``DEALLOCATE`` ``PRINT`` …) carry no table permission
  of their own, but any **SELECT embedded in them** (a cursor's ``FOR SELECT``, an ``IF`` body)
  still requires read access to the real tables it touches.
* **Read-only dynamic SQL** (``EXEC(@sql)``) is allowed *only* when the executed string can be
  statically resolved and proven to be a read-only ``SELECT`` over permitted tables; otherwise
  it is denied with a precise reason. Dynamic SQL never bypasses permissions.

The result is a flat list of :class:`AccessRequirement` over **real** tables plus an optional
``denied_reason`` that must short-circuit to denial regardless of grants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.db.engines import EngineType
from app.services.sql_guard import split_sql_statements
from app.services.sql_introspect import (
    SqlOperation,
    SqlParseError,
    TableRef,
    extract_access,
)

# Statements that are session-local control flow / declarations: they require no table
# permission by themselves (embedded SELECTs are still analyzed separately).
_PROCEDURAL_KEYWORDS = {
    "declare", "set", "if", "else", "while", "begin", "end", "open", "fetch", "close",
    "deallocate", "print", "go", "return", "break", "continue", "goto", "use", "waitfor",
    "throw", "raiserror", "commit", "rollback", "save", "savepoint", "tran", "transaction",
}
# Keywords that introduce dynamic / routine execution — handled specially.
_EXEC_KEYWORDS = {"exec", "execute"}

# A table reference is session-local (no grant needed) if its name is a temp/var or was
# created as a temporary object earlier in the script.
_TEMP_PREFIXES = ("#", "@")

_LEADING_KW_RE = re.compile(r"^\s*([A-Za-z_]+)")
# Real tables referenced after FROM / JOIN / INTO / UPDATE / DELETE FROM — used as a fallback
# when sqlglot can't parse a procedural batch.
_TABLE_REF_RE = re.compile(
    r"\b(?:from|join|into|update|delete\s+from)\s+([#@]?[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)",
    re.IGNORECASE,
)
# CREATE [GLOBAL] TEMP|TEMPORARY TABLE <name>  (PostgreSQL / MySQL temp tables).
_CREATE_TEMP_RE = re.compile(
    r"\bcreate\s+(?:global\s+|local\s+)?temp(?:orary)?\s+table\s+(?:if\s+not\s+exists\s+)?"
    r"([#@]?[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)",
    re.IGNORECASE,
)
# SELECT ... INTO #name  (SQL Server temp table from a query).
# Requires the #/@ prefix so it never matches an ordinary INSERT INTO <real table> (which must
# stay permission-checked); only SQL Server SELECT … INTO #temp registers a session-local name.
_SELECT_INTO_RE = re.compile(r"\binto\s+([#@][A-Za-z_][\w]*)", re.IGNORECASE)
# DECLARE @name TABLE(...)  → table variable.
_DECLARE_TABLE_RE = re.compile(r"\bdeclare\s+(@[A-Za-z_][\w]*)\s+table\b", re.IGNORECASE)
# SET @name = <expr>  → for dynamic-SQL resolution.
_SET_ASSIGN_RE = re.compile(r"^\s*set\s+(@[A-Za-z_][\w]*)\s*=\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)
# EXEC( ... ) or EXEC @var  → dynamic SQL payload.
_EXEC_DYNAMIC_RE = re.compile(
    r"^\s*exec(?:ute)?\s*\(?\s*(@[A-Za-z_][\w]*|N?'(?:[^']|'')*')", re.IGNORECASE
)


@dataclass(frozen=True)
class AccessRequirement:
    operation: SqlOperation
    table: TableRef | None  # a real (non-temp) table, or None when not table-scoped


@dataclass
class ScriptAccess:
    requirements: list[AccessRequirement] = field(default_factory=list)
    denied_reason: str | None = None  # if set, deny regardless of grants


def _leading_keyword(stmt: str) -> str:
    m = _LEADING_KW_RE.match(stmt)
    return m.group(1).lower() if m else ""


def _norm(name: str) -> str:
    """Normalized comparison key: last name component, lowercased, with any #/@ prefix dropped.

    sqlglot strips the leading ``#``/``@`` from temp/var names when it parses them, so we
    compare on the de-prefixed form to match a registered temp object against a parsed ref.
    """
    return name.split(".")[-1].strip().lstrip("#@").lower()


def _is_local(name: str, locals_: set[str]) -> bool:
    bare = name.split(".")[-1].strip().lower()
    if bare.startswith(_TEMP_PREFIXES):  # raw temp/var ref (regex-fallback path)
        return True
    return _norm(name) in locals_  # sqlglot-parsed ref (prefix already stripped)


def _register_local_objects(stmt: str, locals_: set[str]) -> None:
    for rx in (_CREATE_TEMP_RE, _SELECT_INTO_RE, _DECLARE_TABLE_RE):
        for m in rx.finditer(stmt):
            locals_.add(_norm(m.group(1)))


def _resolve_string_expr(expr: str, vars_: dict[str, str]) -> str | None:
    """Resolve a T-SQL string expression built from literals and known @vars joined by ``+``.

    Returns the concatenated string, or ``None`` if any part can't be statically resolved.
    """
    parts = _split_top_level_plus(expr)
    out: list[str] = []
    for raw in parts:
        token = raw.strip()
        if not token:
            return None
        if token.upper().startswith("N'") and token.endswith("'"):
            token = token[1:]
        if token.startswith("'") and token.endswith("'") and len(token) >= 2:
            out.append(token[1:-1].replace("''", "'"))
        elif token.startswith("@") and token.lower() in vars_:
            out.append(vars_[token.lower()])
        else:
            return None  # contains something we can't prove safe (column ref, function, …)
    return "".join(out)


def _split_top_level_plus(expr: str) -> list[str]:
    """Split on ``+`` that are outside string literals."""
    parts, buf, i, n = [], [], 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch == "'":
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(expr[i])
                if expr[i] == "'":
                    if i + 1 < n and expr[i + 1] == "'":
                        buf.append(expr[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "+":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _requirements_from_parsed(
    statements, locals_: set[str]
) -> list[AccessRequirement]:
    """Turn sqlglot statement-access into requirements over REAL (non-local) tables only."""
    reqs: list[AccessRequirement] = []
    for st in statements:
        for t in st.tables:
            if not _is_local(t.name, locals_):
                reqs.append(AccessRequirement(operation=st.operation, table=t))
    return reqs


def _fallback_requirements(stmt: str, locals_: set[str]) -> list[AccessRequirement]:
    """Regex-based extraction when sqlglot can't parse a procedural/temp batch.

    Classifies by leading keyword and pulls real table names from FROM/JOIN/INTO/UPDATE/DELETE.
    Session-local tables are skipped.
    """
    kw = _leading_keyword(stmt)
    op = {
        "select": SqlOperation.SELECT, "with": SqlOperation.SELECT,
        "insert": SqlOperation.INSERT, "update": SqlOperation.UPDATE,
        "delete": SqlOperation.DELETE, "merge": SqlOperation.UPDATE,
    }.get(kw)
    if op is None:
        return []  # procedural/other handled elsewhere
    reqs: list[AccessRequirement] = []
    seen: set[str] = set()
    for m in _TABLE_REF_RE.finditer(stmt):
        name = m.group(1)
        if _is_local(name, locals_):
            continue
        bare = _norm(name)
        if bare in seen:
            continue
        seen.add(bare)
        schema = name.split(".")[0] if "." in name else None
        reqs.append(AccessRequirement(operation=op, table=TableRef(schema=schema, name=name.split(".")[-1])))
    return reqs


def _analyze_dynamic_exec(
    stmt: str, engine: EngineType, vars_: dict[str, str], locals_: set[str], script: ScriptAccess
) -> None:
    """Validate an ``EXEC(@sql)`` / ``EXEC('…')`` as read-only, or set a precise denial."""
    m = _EXEC_DYNAMIC_RE.match(stmt)
    if not m:
        # EXEC <named routine> — routine execution authorization is handled elsewhere; deny
        # here with a clear message rather than silently allowing.
        script.denied_reason = (
            "Executing a stored routine by name is not permitted for your access level."
        )
        return
    token = m.group(1)
    if token.startswith("@"):
        resolved = vars_.get(token.lower())
    else:  # a literal 'string'
        resolved = _resolve_string_expr(token, vars_)
    if resolved is None:
        script.denied_reason = (
            "Dynamic SQL could not be statically verified as read-only and was denied. "
            "Only dynamic SQL that resolves to a read-only SELECT on tables you may read is allowed."
        )
        return
    try:
        inner = extract_access(resolved, engine)
    except SqlParseError:
        script.denied_reason = "Dynamic SQL could not be parsed for validation and was denied."
        return
    for st in inner:
        if st.operation != SqlOperation.SELECT:
            script.denied_reason = (
                f"Dynamic SQL is not read-only (contains {st.operation.value}) and was denied."
            )
            return
    # Read-only dynamic SQL → require SELECT on each real table it reads.
    script.requirements.extend(_requirements_from_parsed(inner, locals_))


def analyze_script_access(sql: str, engine: EngineType) -> ScriptAccess:
    """Analyze a whole script and return the real-table access it requires (+ any denial)."""
    statements = split_sql_statements(sql, engine)
    script = ScriptAccess()
    locals_: set[str] = set()
    vars_: dict[str, str] = {}

    # First pass: register every temp/local object so later references resolve correctly even
    # if they appear before their creator in source order (defensive).
    for stmt in statements:
        _register_local_objects(stmt, locals_)

    for stmt in statements:
        if script.denied_reason:
            return script
        kw = _leading_keyword(stmt)

        # Track SET @x = <expr> for dynamic-SQL resolution (best-effort).
        sm = _SET_ASSIGN_RE.match(stmt)
        if sm:
            resolved = _resolve_string_expr(sm.group(2), vars_)
            vars_[sm.group(1).lower()] = resolved if resolved is not None else None  # type: ignore[assignment]

        if kw in _EXEC_KEYWORDS:
            _analyze_dynamic_exec(stmt, engine, {k: v for k, v in vars_.items() if v is not None}, locals_, script)
            continue

        if kw in _PROCEDURAL_KEYWORDS:
            # No permission of its own — but enforce any embedded SELECT (cursor FOR SELECT,
            # IF body, SET @x = (SELECT …)). sqlglot classifies these batches as a Command and
            # loses the FROM, so extract real tables from the SELECT portion via regex.
            if re.search(r"\bselect\b", stmt, re.IGNORECASE):
                script.requirements.extend(
                    _fallback_requirements(_strip_to_select(stmt), locals_)
                )
            continue

        # Ordinary statement: prefer sqlglot, fall back to regex for unparseable T-SQL.
        try:
            reqs = _requirements_from_parsed(extract_access(stmt, engine), locals_)
        except SqlParseError:
            reqs = _fallback_requirements(stmt, locals_)
        # If this statement creates a session-local temp object (SELECT…INTO #t / CREATE TEMP
        # TABLE … AS SELECT), the only REAL tables it touches are read sources — require SELECT,
        # not the create/insert operation.
        if _CREATE_TEMP_RE.search(stmt) or _SELECT_INTO_RE.search(stmt):
            reqs = [AccessRequirement(SqlOperation.SELECT, r.table) for r in reqs]
        script.requirements.extend(reqs)

    return script


def _strip_to_select(stmt: str) -> str:
    """Return the substring starting at the first SELECT (for cursor/IF embedded selects)."""
    m = re.search(r"\bselect\b", stmt, re.IGNORECASE)
    return stmt[m.start():] if m else stmt
