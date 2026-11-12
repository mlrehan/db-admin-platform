import { test } from "node:test";
import assert from "node:assert/strict";
import {
  analyzeContext,
  buildIndex,
  complete,
  statementBounds,
  tableRefs,
  tokenize,
} from "../core/sql-intellisense.js";

// A catalog shaped like GET /sessions/{id}/catalog (the LAIT sample database).
const CATALOG = {
  dialect: "mssql",
  default_schema: "dbo",
  objects: [
    { schema_name: "dbo", name: "Admission", kind: "table", columns: [
      { name: "AdmissionId", type: "int", nullable: false },
      { name: "StudentId", type: "int", nullable: false },
      { name: "CourseCode", type: "varchar(32)", nullable: true },
    ] },
    { schema_name: "dbo", name: "Address", kind: "table", columns: [{ name: "City", type: "nvarchar(80)", nullable: true }] },
    { schema_name: "dbo", name: "Tutor", kind: "table", columns: [
      { name: "TutorId", type: "int", nullable: false },
      { name: "FirstName", type: "varchar(50)", nullable: true },
      { name: "ManagerId", type: "int", nullable: true },
    ] },
    { schema_name: "dbo", name: "Student", kind: "table", columns: [
      { name: "StudentId", type: "int", nullable: false },
      { name: "Email", type: "varchar(250)", nullable: true },
    ] },
    { schema_name: "sales", name: "Orders", kind: "table", columns: [{ name: "OrderId", type: "int", nullable: false }] },
    { schema_name: "dbo", name: "v_rooms", kind: "view", columns: [{ name: "RoomNo", type: "int", nullable: false }] },
  ],
  routines: [
    { schema_name: "dbo", name: "usp_rooms", kind: "procedure" },
    { schema_name: "dbo", name: "fn_label", kind: "function" },
  ],
};
const INDEX = buildIndex(CATALOG);

function at(sqlWithCursor) {
  const offset = sqlWithCursor.indexOf("|");
  return { text: sqlWithCursor.replace("|", ""), offset };
}
function labels(sqlWithCursor, dialect = "mssql") {
  const { text, offset } = at(sqlWithCursor);
  return complete(text, offset, INDEX, dialect).items.map((i) => i.label);
}

test("FROM A suggests tables starting with A (spec example)", () => {
  const got = labels("SELECT * FROM A|");
  assert.deepEqual(got.slice(0, 2).sort(), ["Address", "Admission"]);
  assert.ok(!got.includes("Tutor"));
});

test("SELECT A. suggests columns of table A (spec example)", () => {
  const got = labels("SELECT Admission.|");
  assert.deepEqual(got, ["*", "AdmissionId", "StudentId", "CourseCode"]);
});

test("an alias resolves to its table even when FROM comes after the cursor", () => {
  assert.deepEqual(labels("SELECT t.| FROM Tutor t"), ["*", "TutorId", "FirstName", "ManagerId"]);
  assert.deepEqual(labels("SELECT s.Em| FROM dbo.Student AS s JOIN Tutor t ON t.TutorId = s.StudentId"), ["Email"]);
});

test("ON / WHERE suggest columns of every table in scope, before functions and keywords", () => {
  const got = labels("SELECT * FROM Tutor t JOIN Student s ON |");
  const firstKeyword = got.indexOf("SELECT");
  assert.ok(got.indexOf("TutorId") >= 0 && got.indexOf("TutorId") < firstKeyword);
  assert.ok(got.indexOf("Email") >= 0 && got.indexOf("Email") < firstKeyword);
  assert.ok(got.includes("t") && got.includes("s")); // aliases offered too
});

test("schema-qualified: dbo. lists that schema's objects and routines", () => {
  const got = labels("SELECT * FROM sales.|");
  assert.deepEqual(got, ["Orders"]);
  assert.ok(labels("EXEC dbo.|").includes("usp_rooms"));
});

test("EXEC suggests stored procedures / functions", () => {
  assert.deepEqual(labels("EXEC usp|"), ["usp_rooms"]);
});

test("INSERT INTO t ( suggests the target table's columns", () => {
  assert.deepEqual(labels("INSERT INTO Student (|"), ["StudentId", "Email"]);
});

test("after a complete FROM table the next word is a keyword, not another table", () => {
  const got = labels("SELECT * FROM Tutor t W|");
  assert.ok(got.includes("WHERE"));
  assert.ok(!got.includes("Tutor"));
});

test("only the statement under the cursor is analysed (; and GO separate statements)", () => {
  const sql = "SELECT * FROM Student s;\nSELECT s.| FROM Tutor s";
  assert.deepEqual(labels(sql), ["*", "TutorId", "FirstName", "ManagerId"]);
  const go = "SELECT * FROM Student s\nGO\nSELECT s.| FROM Tutor s";
  assert.deepEqual(labels(go), ["*", "TutorId", "FirstName", "ManagerId"]);
  const b = statementBounds("a; b; c", 4);
  assert.equal("a; b; c".slice(b.start, b.end).trim(), "b");
});

test("non-default-schema tables are inserted schema-qualified; odd names are quoted", () => {
  const { text, offset } = at("SELECT * FROM Ord|");
  const item = complete(text, offset, INDEX, "mssql").items.find((i) => i.label === "Orders");
  assert.equal(item.insertText, "sales.Orders");
  const idx = buildIndex({ default_schema: "public", objects: [
    { schema_name: "public", name: "order", kind: "table", columns: [] },
    { schema_name: "public", name: "My Table", kind: "table", columns: [] },
  ] });
  const pg = complete("SELECT * FROM ", 14, idx, "postgresql").items;
  assert.equal(pg.find((i) => i.label === "order").insertText, '"order"');
  assert.equal(pg.find((i) => i.label === "My Table").insertText, '"My Table"');
  const my = complete("SELECT * FROM ", 14, idx, "mysql").items;
  assert.equal(my.find((i) => i.label === "My Table").insertText, "`My Table`");
});

test("nothing is suggested inside a string literal or after AS (alias being typed)", () => {
  assert.equal(analyzeContext("SELECT 'abc|", 11, "mssql").kind, "none");
  assert.equal(analyzeContext("SELECT * FROM Tutor AS ", 23, "mssql").kind, "none");
});

test("tableRefs handles joins, comma lists, derived tables and quoted names", () => {
  const refs = tableRefs(tokenize("SELECT * FROM [dbo].[Tutor] t, Student s LEFT JOIN (SELECT 1 x) d ON 1=1", "mssql"));
  assert.deepEqual(
    refs.map((r) => [r.schema, r.name, r.alias]),
    [["dbo", "Tutor", "t"], [null, "Student", "s"], [null, null, "d"]],
  );
});

test("large catalogs stay responsive: 20k tables, capped + incomplete, < 50 ms", () => {
  const objects = Array.from({ length: 20000 }, (_, i) => ({
    schema_name: "dbo", name: `T${String(i).padStart(5, "0")}`, kind: "table",
    columns: [{ name: "id", type: "int", nullable: false }],
  }));
  const big = buildIndex({ default_schema: "dbo", objects, routines: [] });
  const times = [];
  let res;
  for (let r = 0; r < 7; r++) {
    const t0 = performance.now();
    res = complete("SELECT * FROM T1", 16, big, "mssql");
    times.push(performance.now() - t0);
  }
  times.sort((a, b) => a - b);
  const median = times[3]; // one keystroke's worth of work, ignoring JIT warm-up outliers
  assert.ok(res.items.length <= 400);
  assert.ok(res.items.every((i) => i.label.startsWith("T1")));
  assert.ok(median < 50, `median ${median.toFixed(1)} ms (all: ${times.map((t) => t.toFixed(0)).join(",")})`);
});
