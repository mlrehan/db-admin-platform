"""SQL Safety Layer (mandatory).

Classifies SQL prior to execution so the Query Engine can:

1. **Detect destructive operations** (``DROP`` / ``TRUNCATE`` / ``ALTER`` / ``RENAME``).
2. **Enforce role-based restrictions** by mapping each statement to the RBAC permission it
   requires (read → ``query:read``, write → ``query:write``, DDL/destructive/unknown →
   ``query:destructive``).

This is a heuristic classifier, not a full SQL parser, but it is **fail-closed**: literals
and comments are stripped before classification (so a ``DROP`` hidden in a string or comment
can't smuggle past), and anything it cannot confidently classify requires the highest
permission. A statement batch takes the most-privileged category among its statements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.auth.roles import Permission, Role, role_has_permission
from app.core.exceptions import AuthorizationError, ValidationError
from app.models.user import User


class StatementCategory(str, Enum):
    READ = "read"
    WRITE = "write"
    DDL = "ddl"
    TX = "tx"
    UNKNOWN = "unknown"


# Category precedence (higher index = more privileged / takes priority in a batch).
_PRECEDENCE = [
    StatementCategory.READ,
    StatementCategory.TX,
    StatementCategory.WRITE,
    StatementCategory.DDL,
    StatementCategory.UNKNOWN,
]

_REQUIRED_PERMISSION = {
    StatementCategory.READ: Permission.QUERY_READ,
    StatementCategory.TX: Permission.QUERY_READ,
    StatementCategory.WRITE: Permission.QUERY_WRITE,
    StatementCategory.DDL: Permission.QUERY_DESTRUCTIVE,
    StatementCategory.UNKNOWN: Permission.QUERY_DESTRUCTIVE,
}

_READ_KEYWORDS = {"select", "show", "explain", "describe", "desc", "values", "table", "pragma"}
_WRITE_KEYWORDS = {"insert", "update", "delete", "merge", "replace", "upsert", "call", "do"}
_DDL_KEYWORDS = {
    "create", "alter", "drop", "truncate", "rename", "comment",
    "grant", "revoke", "vacuum", "analyze", "reindex", "cluster", "refresh",
}
_DESTRUCTIVE_KEYWORDS = {"drop", "truncate", "alter", "rename"}
_TX_KEYWORDS = {"begin", "start", "commit", "rollback", "savepoint", "set", "reset", "use"}
_DML_RE = re.compile(r"\b(insert|update|delete|merge)\b", re.IGNORECASE)


@dataclass(frozen=True)
class StatementInfo:
    text: str
    leading_keyword: str
    category: StatementCategory
    destructive: bool


@dataclass(frozen=True)
class SqlAnalysis:
    original_sql: str
    statements: list[StatementInfo]
    category: StatementCategory
    destructive: bool
    required_permission: Permission

    @property
    def statement_count(self) -> int:
        return len(self.statements)


def _strip_literals_and_comments(sql: str) -> str:
    """Replace string/identifier literals with spaces and remove comments.

    Handles ``'...'`` (with ``''`` escapes), ``"..."``, backtick identifiers, ``--`` and
    ``#`` line comments, ``/* */`` block comments, and PostgreSQL ``$tag$...$tag$`` dollar
    quoting. The output preserves statement structure (notably ``;``) for splitting.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]
        if two == "--" or ch == "#":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "/*":
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if quote == "'" and i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                i = n if j == -1 else j + len(tag)
                out.append(" ")
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_sql_statements(sql: str) -> list[str]:
    """Split a batch into individual statements on top-level ``;`` preserving original text.

    Comment/literal-aware (so semicolons inside strings or comments don't split). Used by the
    SQL editor's script runner to execute each statement separately.
    """
    statements: list[str] = []
    start = 0
    i, n = 0, len(sql)

    def push(end: int) -> None:
        chunk = sql[start:end].strip()
        if chunk:
            statements.append(chunk)

    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]
        if two == "--" or ch == "#":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "/*":
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if quote == "'" and i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                i = n if j == -1 else j + len(tag)
                continue
        if ch == ";":
            push(i)
            start = i + 1
            i += 1
            continue
        i += 1
    push(n)
    return statements


def split_statements(sql: str) -> list[str]:
    """Split a batch into individual statements on top-level ``;`` (literals/comments-safe)."""
    cleaned = _strip_literals_and_comments(sql)
    parts: list[str] = []
    start = 0
    for idx, ch in enumerate(cleaned):
        if ch == ";":
            chunk = cleaned[start:idx].strip()
            if chunk:
                parts.append(chunk)
            start = idx + 1
    tail = cleaned[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _classify(cleaned_statement: str) -> tuple[str, StatementCategory, bool]:
    tokens = cleaned_statement.strip().split()
    if not tokens:
        return "", StatementCategory.UNKNOWN, False
    keyword = tokens[0].lower().strip("(")
    destructive = keyword in _DESTRUCTIVE_KEYWORDS

    if keyword == "with":
        # A CTE that contains a DML keyword is a write; otherwise it's a read.
        category = StatementCategory.WRITE if _DML_RE.search(cleaned_statement) else StatementCategory.READ
        return keyword, category, destructive
    if keyword in _DDL_KEYWORDS:
        return keyword, StatementCategory.DDL, destructive
    if keyword in _WRITE_KEYWORDS:
        return keyword, StatementCategory.WRITE, destructive
    if keyword in _READ_KEYWORDS:
        return keyword, StatementCategory.READ, destructive
    if keyword in _TX_KEYWORDS:
        return keyword, StatementCategory.TX, destructive
    return keyword, StatementCategory.UNKNOWN, destructive


class SqlGuard:
    def analyze(self, sql: str) -> SqlAnalysis:
        if not sql or not sql.strip():
            raise ValidationError("SQL statement is empty.")
        cleaned_statements = split_statements(sql)
        if not cleaned_statements:
            raise ValidationError("No executable SQL statement found.")

        infos: list[StatementInfo] = []
        for cleaned in cleaned_statements:
            keyword, category, destructive = _classify(cleaned)
            infos.append(
                StatementInfo(
                    text=cleaned,
                    leading_keyword=keyword,
                    category=category,
                    destructive=destructive,
                )
            )

        overall = max(infos, key=lambda s: _PRECEDENCE.index(s.category)).category
        destructive = any(s.destructive for s in infos)
        return SqlAnalysis(
            original_sql=sql,
            statements=infos,
            category=overall,
            destructive=destructive,
            required_permission=_REQUIRED_PERMISSION[overall],
        )

    def enforce(self, user: User, analysis: SqlAnalysis) -> None:
        """Raise :class:`AuthorizationError` if ``user`` may not run this SQL."""
        if not role_has_permission(Role(user.role), analysis.required_permission):
            raise AuthorizationError(
                "Your role is not permitted to run this kind of statement.",
                details={
                    "category": analysis.category.value,
                    "destructive": analysis.destructive,
                    "required_permission": analysis.required_permission.value,
                },
            )

    def analyze_and_enforce(self, user: User, sql: str) -> SqlAnalysis:
        analysis = self.analyze(sql)
        self.enforce(user, analysis)
        return analysis
