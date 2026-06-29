"""Script-aware access analysis: temp tables, table variables, procedural T-SQL, cursors and
read-only dynamic SQL must be allowed for a SELECT-granted user; writes / unsafe dynamic SQL
must be denied."""

from __future__ import annotations

import pytest

from app.core.exceptions import AuthorizationError
from app.db.engines import EngineType
from app.services.access_control import AccessPolicy, GrantSpec
from app.services.sql_introspect import SqlOperation
from app.services.sql_script_analyzer import analyze_script_access

MSSQL = EngineType.MSSQL


def _select_only_policy() -> AccessPolicy:
    # SELECT on any database/table (what the failing users had).
    grant = GrantSpec(operations=frozenset({SqlOperation.SELECT}), databases=(), tables=())
    return AccessPolicy(is_admin=False, has_grants=True, grants=(grant,))


# --- the concrete scripts from the bug report (must be ALLOWED with SELECT) ---------------

TEMP_TABLE_SCRIPT = """
SELECT StatusId, COUNT(*) AS CountTutors INTO #TutorsByStatus FROM Tutor GROUP BY StatusId;
SELECT StatusId, COUNT(*) AS CountStudents INTO #StudentsByStatus FROM Student GROUP BY StatusId;
SELECT sl.StatusId, StatusName, CountTutors, CountStudents FROM StatusLookup AS sl
LEFT JOIN #TutorsByStatus AS t ON t.StatusId = sl.StatusId
LEFT JOIN #StudentsByStatus AS s ON s.StatusId = sl.StatusId;
DROP TABLE #TutorsByStatus; DROP TABLE #StudentsByStatus;
"""

DYNAMIC_SQL_SCRIPT = """
DECLARE @sql nvarchar(1000); DECLARE @tId varchar(10);
SET @tId = '4';
SET @sql = 'SELECT TutorId, FirstName, LastName, DateStarted FROM Tutor WHERE TutorId = ' + @tId;
EXEC (@sql);
"""

TABLE_VAR_SCRIPT = """
DECLARE @ListOfSeasons TABLE(ID int, SeasonName varchar(40), Details varchar(40));
INSERT INTO @ListOfSeasons VALUES (1,'Spring','x'),(2,'Summer','y');
SELECT * FROM @ListOfSeasons;
"""

SCALAR_IF_SCRIPT = """
DECLARE @score int; SET @score = 68;
IF (@score > 50) SELECT 'You have got the pass mark.';
ELSE SELECT 'Sorry, you have failed.';
"""

CURSOR_SCRIPT = """
DECLARE tutor_cursor CURSOR FOR SELECT TutorId, FirstName, LastName FROM Tutor;
OPEN tutor_cursor;
WHILE @@FETCH_STATUS = 0 FETCH NEXT FROM tutor_cursor;
CLOSE tutor_cursor; DEALLOCATE tutor_cursor;
"""


@pytest.mark.parametrize(
    "script",
    [TEMP_TABLE_SCRIPT, DYNAMIC_SQL_SCRIPT, TABLE_VAR_SCRIPT, SCALAR_IF_SCRIPT, CURSOR_SCRIPT],
)
def test_read_only_scripts_allowed_with_select(script: str) -> None:
    # No denial raised → the SELECT-only user may run it.
    _select_only_policy().enforce_script(MSSQL, "appdb", script)


def test_temp_tables_need_no_grant_but_sources_need_select() -> None:
    access = analyze_script_access(TEMP_TABLE_SCRIPT, MSSQL)
    assert access.denied_reason is None
    by_table = {(r.operation, r.table.name if r.table else None) for r in access.requirements}
    # Only the REAL source tables are required (SELECT); temp tables carry no requirement.
    assert (SqlOperation.SELECT, "Tutor") in by_table
    assert (SqlOperation.SELECT, "Student") in by_table
    assert (SqlOperation.SELECT, "StatusLookup") in by_table
    assert not any(t and t.startswith(("#", "TutorsByStatus", "StudentsByStatus")) for _, t in by_table)


def test_cursor_select_source_is_required() -> None:
    access = analyze_script_access(CURSOR_SCRIPT, MSSQL)
    assert (SqlOperation.SELECT, "Tutor") in {
        (r.operation, r.table.name if r.table else None) for r in access.requirements
    }


# --- safety: writes and unsafe dynamic SQL must still be DENIED for a SELECT-only user ----


def test_write_in_script_denied() -> None:
    policy = _select_only_policy()
    with pytest.raises(AuthorizationError) as exc:
        policy.enforce_script(MSSQL, "appdb", "SELECT * INTO #t FROM Tutor; DELETE FROM Tutor;")
    assert exc.value.code == "ACCESS_DENIED"
    assert "DELETE" in str(exc.value)


def test_dynamic_sql_not_read_only_denied() -> None:
    script = "DECLARE @s nvarchar(100); SET @s = 'DROP TABLE Tutor'; EXEC(@s);"
    with pytest.raises(AuthorizationError):
        _select_only_policy().enforce_script(MSSQL, "appdb", script)


def test_unresolvable_dynamic_sql_denied() -> None:
    # @s is never assigned a statically-known value → cannot be proven read-only → deny.
    with pytest.raises(AuthorizationError) as exc:
        _select_only_policy().enforce_script(MSSQL, "appdb", "EXEC(@s);")
    assert exc.value.code == "ACCESS_DENIED"


def test_exec_named_routine_denied_for_non_admin() -> None:
    with pytest.raises(AuthorizationError):
        _select_only_policy().enforce_script(MSSQL, "appdb", "EXEC dbo.SomeProcedure;")


def test_real_insert_still_requires_grant() -> None:
    access = analyze_script_access("INSERT INTO Student VALUES (1);", MSSQL)
    assert (SqlOperation.INSERT, "Student") in {
        (r.operation, r.table.name if r.table else None) for r in access.requirements
    }


def test_admin_bypasses_script_enforcement() -> None:
    admin = AccessPolicy(is_admin=True, has_grants=False, grants=())
    admin.enforce_script(MSSQL, "appdb", "DROP TABLE Tutor; DELETE FROM Student;")  # no raise
