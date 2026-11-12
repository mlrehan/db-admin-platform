import { test } from "node:test";
import assert from "node:assert/strict";
import {
  builtinFunctions,
  callContext,
  enumValues,
  functionSnippet,
  parseSpec,
  paramDoc,
  parameterValueItems,
  routineSignature,
  signatureHelp,
} from "../core/sql-functions.js";

const at = (sql) => ({ text: sql.replace("|", ""), offset: sql.indexOf("|") });
function hint(sql, dialect) {
  const { text, offset } = at(sql);
  const call = callContext(text, offset, dialect);
  if (!call) return null;
  const sigs = builtinFunctions(dialect).get(call.name.toUpperCase());
  const help = signatureHelp(call, sigs);
  if (!help) return { call };
  const sig = help.signatures[help.activeSignature];
  return { call, sig, param: sig.params[help.activeParameter], help };
}

test("every built-in spec parses and each parameter highlight points at its own text", () => {
  for (const dialect of ["mssql", "postgresql", "mysql", "sqlite"]) {
    const fns = builtinFunctions(dialect);
    assert.ok(fns.size > 60, `${dialect} has a broad catalog (${fns.size})`);
    for (const [name, sigs] of fns) {
      for (const sig of sigs) {
        assert.ok(sig.label.startsWith(`${name}(`), `${dialect} ${sig.label}`);
        assert.ok(!sig.label.includes(":@"), "enum annotations are hidden from the label");
        for (const p of sig.params) {
          assert.equal(sig.label.slice(p.start, p.end), p.label, `${dialect} ${sig.label} → ${p.label}`);
          if (p.enumRef) assert.ok(enumValues(p.enumRef)?.values.length, `enum ${p.enumRef} exists`);
        }
      }
    }
  }
});

test("DATEADD: label, parameter names, datepart values and snippet", () => {
  const [sig] = builtinFunctions("mssql").get("DATEADD");
  assert.equal(sig.label, "DATEADD(datepart, number, date)");
  assert.deepEqual(sig.params.map((p) => p.name), ["datepart", "number", "date"]);
  assert.equal(sig.params[0].enumRef, "mssql_datepart");
  assert.ok(enumValues("mssql_datepart").values.some(([v]) => v === "month"));
  assert.match(paramDoc(sig.params[0]), /One of: year, quarter, month/);
  // The datepart is a dropdown of allowed values (snippet choice); the rest are named placeholders.
  assert.equal(
    functionSnippet(sig),
    "DATEADD(${1|day,month,year,week,quarter,dayofyear,weekday,hour,minute,second,millisecond,microsecond,nanosecond|}, ${2:number}, ${3:date})"
  );
  // Commas inside choice values are escaped.
  const [conv] = builtinFunctions("mssql").get("CONVERT");
  assert.ok(functionSnippet(conv).includes("decimal(18\\, 2)"));
});

test("active parameter follows commas, ignoring nested calls, strings and comments", () => {
  assert.equal(hint("SELECT DATEADD(|", "mssql").param.name, "datepart");
  assert.equal(hint("SELECT DATEADD(day, |", "mssql").param.name, "number");
  assert.equal(hint("SELECT DATEADD(day, 1, |", "mssql").param.name, "date");
  // Commas inside a nested call or a string don't move the parameter.
  assert.equal(hint("SELECT DATEADD(day, ABS(-3), |", "mssql").param.name, "date");
  assert.equal(hint("SELECT DATEADD(day, LEN('a,b,c'), |", "mssql").param.name, "date");
  assert.equal(hint("SELECT DATEADD(day /* x, y */, |", "mssql").param.name, "number");
  // Inside the nested call, the inner function is active.
  const inner = hint("SELECT DATEADD(day, ABS(|", "mssql");
  assert.equal(inner.call.name, "ABS");
  // After the call is closed there is no hint.
  assert.equal(hint("SELECT DATEADD(day, 1, GETDATE()) |", "mssql"), null);
});

test("keyword separators: CAST … AS, EXTRACT … FROM, POSITION … IN", () => {
  assert.equal(hint("SELECT CAST(x AS |", "mssql").param.name, "data_type");
  assert.equal(hint("SELECT CAST(x AS |", "mssql").param.enumRef, "mssql_type");
  assert.equal(hint("SELECT EXTRACT(|", "postgresql").param.enumRef, "pg_field");
  assert.equal(hint("SELECT EXTRACT(year FROM |", "postgresql").param.name, "source");
  assert.equal(hint("SELECT POSITION('a' IN |", "postgresql").param.name, "string");
  // A keyword separator in an unrelated position doesn't count (x IN (…) inside IIF).
  assert.equal(hint("SELECT IIF(x IN (1, 2), |", "mssql").param.name, "true_value");
});

test("overload selection: TRIM(chars FROM str) vs TRIM(str); CONVERT style is optional", () => {
  assert.equal(hint("SELECT TRIM(|", "mssql").sig.label, "TRIM(string)");
  assert.equal(hint("SELECT TRIM('x' FROM |", "mssql").sig.label, "TRIM(characters FROM string)");
  const conv = hint("SELECT CONVERT(varchar(10), d, |", "mssql");
  assert.equal(conv.param.name, "style");
  assert.ok(conv.param.optional);
  assert.equal(conv.param.enumRef, "mssql_style");
});

test("variadic parameters stay highlighted on the last parameter", () => {
  const h = hint("SELECT CONCAT(a, b, c, d, |", "mssql");
  assert.equal(h.param.name, "string_valueN");
  assert.ok(h.param.variadic);
});

test("dialects have their own functions and value sets", () => {
  assert.equal(hint("SELECT DATE_TRUNC(|", "postgresql").param.enumRef, "pg_trunc");
  assert.ok(enumValues("pg_trunc").quote, "date_trunc fields are string literals");
  assert.equal(hint("SELECT TIMESTAMPDIFF(|", "mysql").param.enumRef, "my_unit");
  assert.equal(hint("SELECT STRFTIME(|", "sqlite").param.enumRef, "sqlite_format");
  assert.equal(hint("SELECT date('now', |", "sqlite").param.enumRef, "sqlite_modifier");
  assert.equal(builtinFunctions("sqlite").get("DATEADD"), undefined);
});

test("cursor inside an open string literal is detected", () => {
  const { text, offset } = at("SELECT DATE_TRUNC('mo|");
  const call = callContext(text, offset, "postgresql");
  assert.equal(call.name, "DATE_TRUNC");
  assert.equal(call.inString, true);
  const closed = at("SELECT DATE_TRUNC('month', |");
  assert.equal(callContext(closed.text, closed.offset, "postgresql").inString, false);
});

test("window functions are inserted with an OVER clause", () => {
  const [sig] = builtinFunctions("mssql").get("ROW_NUMBER");
  assert.equal(functionSnippet(sig), "ROW_NUMBER() OVER (${1:ORDER BY })");
  const [lag] = builtinFunctions("postgresql").get("LAG");
  assert.equal(functionSnippet(lag), "LAG(${1:scalar_expression}) OVER (${2:ORDER BY })");
});

test("T-SQL EXEC procedure arguments and user-defined routine signatures", () => {
  const { text, offset } = at("EXEC dbo.usp_GetOrders @CustomerId = 5, |");
  const call = callContext(text, offset, "mssql");
  assert.equal(call.name, "usp_GetOrders");
  assert.equal(call.schema, "dbo");
  assert.ok(call.procedureCall);
  const sig = routineSignature({ schema_name: "dbo", name: "usp_GetOrders", kind: "procedure",
    params: ["@CustomerId int", "@From date"] }, true);
  const help = signatureHelp(call, [sig]);
  assert.equal(help.activeParameter, 1);
  assert.equal(sig.label.slice(sig.params[1].start, sig.params[1].end), "@From date");
});

test("parseSpec handles optional groups and DISTINCT prefixes", () => {
  const s = parseSpec("LAG(scalar_expression [, offset [, default]]) OVER -> same | Previous row.");
  assert.deepEqual(s.params.map((p) => [p.name, p.optional]), [
    ["scalar_expression", false], ["offset", true], ["default", true],
  ]);
  assert.ok(s.window);
  const c = parseSpec("COUNT([DISTINCT] expression) -> int | Count.");
  assert.equal(c.params[0].name, "expression");
});

test("parameter values are offered for enumerated parameters only", () => {
  const items = (sql, dialect) => {
    const { text, offset } = at(sql);
    return parameterValueItems(callContext(text, offset, dialect), dialect);
  };
  const dp = items("SELECT DATEADD(|", "mssql");
  assert.equal(dp.param.name, "datepart");
  assert.ok(dp.items.some((i) => i.label === "month" && /Month/.test(i.detail)));
  assert.equal(items("SELECT DATEADD(day, |", "mssql"), null, "number is free-form");
  // Quoted values: wrapped at a fresh argument, bare inside an open string.
  assert.ok(items("SELECT DATE_TRUNC(|", "postgresql").items.some((i) => i.insertText === "'month'"));
  assert.ok(items("SELECT DATE_TRUNC('mo|", "postgresql").items.some((i) => i.insertText === "month"));
  assert.equal(items("SELECT DATEADD('x|", "mssql"), null, "bare values never inside a string");
  assert.ok(items("SELECT CAST(Price AS |", "mssql").items.some((i) => i.label === "decimal(18, 2)"));
});

test("user routine parameters are described from their declaration", () => {
  const sig = routineSignature({ name: "p", kind: "procedure", params: ["@Id int", "@Total money OUTPUT", "n integer DEFAULT 1", "OUT r text"] }, true);
  assert.deepEqual(sig.params.map((p) => [p.name, paramDoc(p)]), [
    ["@Id", "Type: int"],
    ["@Total", "Type: money · output parameter (returns a value)"],
    ["n", "Type: integer · default 1"],
    ["r", "Type: text · output parameter (returns a value)"],
  ]);
});
