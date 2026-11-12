"""Client-side script structure: SQL Server GO batches, MySQL DELIMITER, compound bodies."""

from __future__ import annotations

import pytest

from app.db.engines import EngineType as E
from app.services.sql_batches import (
    BatchParseError,
    has_go_separator,
    split_go_batches,
    split_statements,
    strip_go_lines,
)

USER_SCRIPT = """CREATE DATABASE LAIT;
GO

USE LAIT;
GO

CREATE TABLE Room
(
    RoomNo INT NOT NULL,
    RoomName VARCHAR(50),
    CONSTRAINT pk_Room PRIMARY KEY (RoomNo)
);
GO
"""


def test_user_script_splits_into_three_batches_with_lines() -> None:
    batches = split_go_batches(USER_SCRIPT)
    assert [b.sql.splitlines()[0] for b in batches] == [
        "CREATE DATABASE LAIT;", "USE LAIT;", "CREATE TABLE Room",
    ]
    assert [b.start_line for b in batches] == [1, 3, 6]
    assert all("GO" not in b.sql.split() for b in batches)  # GO is never sent


def test_go_inside_strings_comments_and_brackets_is_not_a_separator() -> None:
    script = (
        "INSERT INTO t VALUES ('a\nGO\nb');\n"
        "/* outer /* nested\nGO\n*/ still comment\nGO\n*/\n"
        "SELECT [col\nGO\nx] FROM t   -- trailing\n"
        "go 3\n"
        "PRINT 'done'\n"
        "  Go  -- a comment after GO is allowed\n"
    )
    batches = split_go_batches(script)
    assert len(batches) == 2
    assert batches[0].repeat == 3
    assert "nested" in batches[0].sql and "[col\nGO\nx]" in batches[0].sql
    assert batches[1].sql == "PRINT 'done'"


def test_go_must_be_alone_on_its_line() -> None:
    # "GO" as part of other text is not a separator.
    assert len(split_go_batches("SELECT 1 AS GO\nSELECT 'GO'\nEXEC GOTO_PROC")) == 1
    assert not has_go_separator("SELECT 1 -- GO\nSELECT 2")
    assert has_go_separator("SELECT 1\nGO")


def test_go_repeat_count_and_limits() -> None:
    assert split_go_batches("INSERT INTO t DEFAULT VALUES\nGO 25")[0].repeat == 25
    with pytest.raises(BatchParseError):
        split_go_batches("SELECT 1\nGO 0")
    with pytest.raises(BatchParseError):
        split_go_batches("SELECT 1\nGO 1000000")


def test_crlf_and_empty_batches() -> None:
    batches = split_go_batches("SELECT 1\r\nGO\r\n\r\nGO\r\nSELECT 2\r\n")
    assert [b.sql for b in batches] == ["SELECT 1", "SELECT 2"]


def test_strip_go_lines_exposes_every_statement_for_analysis() -> None:
    flat = strip_go_lines("SELECT 1\nGO\nDROP TABLE Users\nGO")
    assert "DROP TABLE Users" in flat and "\nGO" not in flat


# --- statement splitting -----------------------------------------------------------------

MYSQL_PROC = r"""CREATE PROCEDURE p(IN x INT)
BEGIN
  DECLARE y INT DEFAULT 0;
  IF x > 1 THEN SET y = CASE WHEN x > 5 THEN 2 ELSE 1 END; END IF;
  WHILE y < 3 DO SET y = y + 1; END WHILE;
  SELECT y, 'it\'s; fine';
END;
SELECT 2;"""


def test_mysql_procedure_body_kept_whole() -> None:
    parts = split_statements(MYSQL_PROC, E.MYSQL)
    assert len(parts) == 2
    assert parts[0].startswith("CREATE PROCEDURE") and parts[0].rstrip().endswith("END")
    assert parts[1] == "SELECT 2"


def test_mysql_delimiter_directive() -> None:
    script = (
        "DELIMITER //\n"
        "CREATE PROCEDURE q() BEGIN SELECT 1; SELECT 2; END //\n"
        "DELIMITER ;\n"
        "CALL q();\nSELECT 3;"
    )
    assert split_statements(script, E.MYSQL) == [
        "CREATE PROCEDURE q() BEGIN SELECT 1; SELECT 2; END", "CALL q()", "SELECT 3",
    ]


def test_mysql_definer_trigger_body() -> None:
    script = (
        "CREATE DEFINER=`root`@`%` TRIGGER trg BEFORE INSERT ON t FOR EACH ROW "
        "BEGIN SET NEW.a = 1; SET NEW.b = 2; END; INSERT INTO t VALUES (1);"
    )
    assert len(split_statements(script, E.MYSQL)) == 2


def test_compound_body_detected_when_not_the_first_statement() -> None:
    # Regression: the CREATE TRIGGER/PROCEDURE header must be recognised anywhere in a script,
    # not only at its very start.
    script = (
        "CREATE TABLE a (x INT);\nINSERT INTO a VALUES (1);\n"
        "CREATE TRIGGER tr AFTER INSERT ON a BEGIN\n"
        "  INSERT INTO log VALUES ('x');\n  INSERT INTO log VALUES ('y;z');\nEND;\n"
        "SELECT 1;"
    )
    parts = split_statements(script, None)
    assert len(parts) == 4
    assert parts[2].startswith("CREATE TRIGGER") and parts[2].endswith("END")
    my = "SELECT 1;\nCREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END;\nCALL p();"
    assert len(split_statements(my, E.MYSQL)) == 3


def test_sqlite_trigger_body() -> None:
    script = (
        "CREATE TRIGGER tr AFTER INSERT ON a BEGIN INSERT INTO log VALUES (1); "
        "UPDATE c SET n = n + 1; END; SELECT 1;"
    )
    assert len(split_statements(script, None)) == 2


def test_postgres_dollar_quote_escape_string_and_atomic() -> None:
    script = (
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql; "
        r"SELECT E'a\';b'; SELECT 3;"
    )
    assert split_statements(script, E.POSTGRESQL) == [
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql",
        r"SELECT E'a\';b'",
        "SELECT 3",
    ]
    atomic = (
        "CREATE PROCEDURE p() LANGUAGE SQL BEGIN ATOMIC INSERT INTO t VALUES (1); "
        "INSERT INTO t VALUES (2); END; SELECT 1;"
    )
    assert len(split_statements(atomic, E.POSTGRESQL)) == 2


def test_plain_statements_and_literals() -> None:
    assert split_statements("SELECT 1; INSERT INTO t VALUES (';'); UPDATE t SET a = 1", E.POSTGRESQL) == [
        "SELECT 1", "INSERT INTO t VALUES (';')", "UPDATE t SET a = 1",
    ]
    # A CASE expression in an ordinary statement doesn't affect splitting.
    assert len(split_statements("SELECT CASE WHEN 1=1 THEN 'a' END; SELECT 2;", E.MYSQL)) == 2
    # '#' is a comment only in MySQL (SQL Server temp tables use it).
    assert split_statements("SELECT * INTO #t FROM a; SELECT * FROM #t;", E.MSSQL) == [
        "SELECT * INTO #t FROM a", "SELECT * FROM #t",
    ]
