import { test } from "node:test";
import assert from "node:assert/strict";
import { quoteIdent, qualifiedName, buildSelect } from "../core/sql.js";

test("quoteIdent per engine", () => {
  assert.equal(quoteIdent("postgresql", "users"), '"users"');
  assert.equal(quoteIdent("mysql", "users"), "`users`");
  assert.equal(quoteIdent("mssql", "users"), "[users]");
});

test("quoteIdent escapes the closing quote", () => {
  assert.equal(quoteIdent("postgresql", 'we"ird'), '"we""ird"');
  assert.equal(quoteIdent("mysql", "ba`d"), "`ba``d`");
  assert.equal(quoteIdent("mssql", "ev]il"), "[ev]]il]");
});

test("qualifiedName includes schema when present", () => {
  assert.equal(qualifiedName("postgresql", "public", "users"), '"public"."users"');
  assert.equal(qualifiedName("postgresql", null, "users"), '"users"');
});

test("buildSelect for postgres uses LIMIT/OFFSET", () => {
  const sql = buildSelect({
    engine: "postgresql", schema: "public", table: "t", limit: 50, offset: 100,
  });
  assert.equal(sql, 'SELECT * FROM "public"."t" LIMIT 50 OFFSET 100');
});

test("buildSelect with order by", () => {
  const sql = buildSelect({
    engine: "mysql", schema: null, table: "t", orderBy: "id", direction: "desc",
    limit: 10, offset: 0,
  });
  assert.equal(sql, "SELECT * FROM `t` ORDER BY `id` DESC LIMIT 10 OFFSET 0");
});

test("buildSelect for mssql uses OFFSET/FETCH and synthesizes ORDER BY", () => {
  const sql = buildSelect({ engine: "mssql", schema: "dbo", table: "t", limit: 25, offset: 50 });
  assert.equal(
    sql,
    "SELECT * FROM [dbo].[t] ORDER BY (SELECT NULL) OFFSET 50 ROWS FETCH NEXT 25 ROWS ONLY"
  );
});

test("buildSelect sanitizes non-integer limit/offset", () => {
  const sql = buildSelect({
    engine: "postgresql", schema: null, table: "t", limit: "abc", offset: -5,
  });
  assert.equal(sql, 'SELECT * FROM "t" LIMIT 100 OFFSET 0');
});
