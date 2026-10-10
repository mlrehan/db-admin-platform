import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PlaygroundDatabase,
  INITIAL_PLAYGROUND_QUERY,
  SAMPLE_QUERIES,
  splitSqlStatements,
} from "../modules/playground/sqlite-engine.js";

test("splitSqlStatements handles strings, escaped quotes, and comments with semicolons", () => {
  const sql = `
    -- Comment with semicolon ; inside
    SELECT 'hello;world', 'it''s;fine' FROM student;
    /* Block comment; with; semicolons */
    UPDATE "student" SET name = 'Bob' WHERE id = 2;
  `;
  const stmts = splitSqlStatements(sql);
  assert.equal(stmts.length, 2);
  assert.match(stmts[0], /^SELECT 'hello;world', 'it''s;fine' FROM student$/);
  assert.match(stmts[1], /^UPDATE "student" SET name = 'Bob' WHERE id = 2$/);
});

test("PlaygroundDatabase initializes with course, student, and admission tables and foreign keys", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  const schema = db.getSchema();
  assert.equal(schema.length, 3);
  assert.deepEqual(
    schema.map((t) => t.name),
    ["course", "student", "admission"]
  );

  const course = schema.find((t) => t.name === "course");
  assert.equal(course.rowCount, 5);
  assert.deepEqual(
    course.columns.map((c) => ({ name: c.name, type: c.type, pk: c.primaryKey })),
    [
      { name: "id", type: "INTEGER", pk: true },
      { name: "name", type: "TEXT", pk: false },
    ]
  );

  const student = schema.find((t) => t.name === "student");
  assert.equal(student.rowCount, 8);
  assert.deepEqual(
    student.columns.map((c) => ({ name: c.name, type: c.type, pk: c.primaryKey })),
    [
      { name: "id", type: "INTEGER", pk: true },
      { name: "name", type: "TEXT", pk: false },
      { name: "age", type: "INTEGER", pk: false },
    ]
  );

  const admission = schema.find((t) => t.name === "admission");
  assert.equal(admission.rowCount, 12);
  assert.deepEqual(
    admission.columns.map((c) => ({
      name: c.name,
      type: c.type,
      pk: c.primaryKey,
      fk: c.foreignKey?.referredTable || null,
    })),
    [
      { name: "id", type: "INTEGER", pk: true, fk: null },
      { name: "student_id", type: "INTEGER", pk: false, fk: "student" },
      { name: "course_id", type: "INTEGER", pk: false, fk: "course" },
      { name: "admission_date", type: "TEXT", pk: false, fk: null },
    ]
  );

  db.close();
});

test("initial SELECT query returns actual seeded student records", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  assert.equal(INITIAL_PLAYGROUND_QUERY, "SELECT * FROM student;");
  const res = db.execute(INITIAL_PLAYGROUND_QUERY);
  assert.equal(res.ok, true);
  assert.equal(res.resultSets.length, 1);
  assert.equal(res.totalRowsReturned, 8);

  const rs = res.resultSets[0];
  assert.deepEqual(rs.columns, ["id", "name", "age"]);
  assert.equal(rs.rows.length, 8);
  assert.deepEqual(rs.rows[0], [1, "Alice Johnson", 17]);
  assert.deepEqual(rs.rows[7], [8, "Henry Taylor", 16]);

  db.close();
});

test("filtering, sorting, and zero-row SELECT queries return accurate columns and rows", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  const filtered = db.execute(
    "SELECT name, age FROM student WHERE age = 18 ORDER BY name DESC;"
  );
  assert.equal(filtered.ok, true);
  assert.deepEqual(filtered.resultSets[0].columns, ["name", "age"]);
  assert.deepEqual(filtered.resultSets[0].rows, [
    ["Frank Miller", 18],
    ["Carol Davis", 18],
  ]);

  // A SELECT matching 0 rows still returns column headers with 0 rows.
  const emptySelect = db.execute("SELECT id, name FROM course WHERE id = 999;");
  assert.equal(emptySelect.ok, true);
  assert.equal(emptySelect.resultSets.length, 1);
  assert.deepEqual(emptySelect.resultSets[0].columns, ["id", "name"]);
  assert.deepEqual(emptySelect.resultSets[0].rows, []);
  assert.equal(emptySelect.totalRowsReturned, 0);

  db.close();
});

test("JOIN queries across student, course, and admission return relational results", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  const joinRes = db.execute(`
    SELECT s.name AS student, c.name AS course, a.admission_date
    FROM admission a
    JOIN student s ON a.student_id = s.id
    JOIN course c ON a.course_id = c.id
    WHERE s.id = 1
    ORDER BY c.id ASC;
  `);
  assert.equal(joinRes.ok, true);
  assert.deepEqual(joinRes.resultSets[0].columns, ["student", "course", "admission_date"]);
  assert.deepEqual(joinRes.resultSets[0].rows, [
    ["Alice Johnson", "Mathematics", "2024-09-01"],
    ["Alice Johnson", "Computer Science", "2024-09-01"],
  ]);

  db.close();
});

test("INSERT, UPDATE, DELETE, and CREATE TABLE modify database and report non-row status", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  // INSERT
  const ins = db.execute("INSERT INTO course (id, name) VALUES (6, 'Astronomy');");
  assert.equal(ins.ok, true);
  assert.equal(ins.resultSets.length, 0);
  assert.equal(ins.totalRowsAffected, 1);
  assert.equal(ins.statements[0].returnsRows, false);
  assert.equal(ins.statements[0].rowsAffected, 1);

  // UPDATE
  const upd = db.execute("UPDATE student SET age = 19 WHERE id IN (1, 2);");
  assert.equal(upd.ok, true);
  assert.equal(upd.resultSets.length, 0);
  assert.equal(upd.totalRowsAffected, 2);

  // DELETE
  const del = db.execute("DELETE FROM admission WHERE id = 12;");
  assert.equal(del.ok, true);
  assert.equal(del.totalRowsAffected, 1);

  // CREATE TABLE updates live schema introspection and reports 0 rowsAffected
  const ddl = db.execute("CREATE TABLE department (id INTEGER PRIMARY KEY, title TEXT NOT NULL);");
  assert.equal(ddl.ok, true);
  assert.equal(ddl.totalRowsAffected, 0);
  assert.equal(ddl.statements[0].rowsAffected, 0);
  const schemaAfterDdl = db.getSchema();
  assert.equal(schemaAfterDdl.length, 4);
  assert.ok(schemaAfterDdl.some((t) => t.name === "department"));

  db.close();
});

test("foreign key constraints are enforced on admission table", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  // Invalid student_id (999 does not exist) must fail due to PRAGMA foreign_keys = ON
  const badFk = db.execute(
    "INSERT INTO admission (id, student_id, course_id, admission_date) VALUES (99, 999, 1, '2024-10-01');"
  );
  assert.equal(badFk.ok, false);
  assert.match(badFk.error, /FOREIGN KEY constraint failed/i);

  db.close();
});

test("invalid SQL and empty queries produce helpful error states", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  const empty1 = db.execute("   ");
  assert.equal(empty1.ok, false);
  assert.equal(empty1.empty, true);
  assert.match(empty1.error, /Please enter a SQL query/i);

  const emptyComments = db.execute("-- only a comment\n/* block comment */");
  assert.equal(emptyComments.ok, false);
  assert.equal(emptyComments.empty, true);

  const badSql = db.execute("SELEC * FORM non_existent_table;");
  assert.equal(badSql.ok, false);
  assert.ok(badSql.error && badSql.error.length > 0);

  db.close();
});

test("reset() restores original schema and deterministic sample data", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  // Mutate schema and data destructively
  db.execute("DROP TABLE admission; DELETE FROM student; CREATE TABLE temp_notes (note TEXT);");
  assert.equal(db.getSchema().some((t) => t.name === "admission"), false);
  assert.equal(db.getSchema().some((t) => t.name === "temp_notes"), true);

  // Reset database
  const restoredSchema = db.reset();
  assert.deepEqual(
    restoredSchema.map((t) => ({ name: t.name, rows: t.rowCount })),
    [
      { name: "course", rows: 5 },
      { name: "student", rows: 8 },
      { name: "admission", rows: 12 },
    ]
  );

  const checkStudents = db.execute(INITIAL_PLAYGROUND_QUERY);
  assert.equal(checkStudents.ok, true);
  assert.equal(checkStudents.totalRowsReturned, 8);

  db.close();
});

test("all preset SAMPLE_QUERIES execute cleanly against the seeded database", async () => {
  const db = new PlaygroundDatabase();
  await db.init();

  for (const sample of SAMPLE_QUERIES) {
    const res = db.execute(sample.sql);
    assert.equal(res.ok, true, `Sample query "${sample.id}" failed: ${res.error}`);
    assert.ok(res.resultSets.length >= 1);
  }

  db.close();
});
