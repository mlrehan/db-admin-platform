import { test } from "node:test";
import assert from "node:assert/strict";
import { cellHtml, compareCells, csvValue, estimateColumnWidth, formatCell, toCsv } from "../core/cell-format.js";

test("NULL, empty string and falsy values are all distinct", () => {
  assert.deepEqual([formatCell(null).text, formatCell(null).cls], ["NULL", "cell-null"]);
  assert.deepEqual([formatCell(undefined).text, formatCell(undefined).cls], ["NULL", "cell-null"]);
  assert.deepEqual([formatCell("").text, formatCell("").cls], ["(empty)", "cell-empty"]);
  // 0 / 0.0 / false / "0" are real values, never NULL or empty.
  assert.deepEqual([formatCell(0, "integer").text, formatCell(0, "integer").cls], ["0", "cell-num"]);
  assert.equal(formatCell(0.0, "float").text, "0");
  assert.deepEqual([formatCell(false).text, formatCell(false).cls], ["false", "cell-bool is-false"]);
  assert.equal(formatCell(true).text, "true");
  assert.equal(formatCell("0", "text").text, "0");
  assert.equal(formatCell("NULL", "text").cls, "cell-text", "the string 'NULL' is not SQL NULL");
});

test("whitespace-only strings are made visible", () => {
  assert.equal(formatCell("   ").text, "(3 whitespace chars)");
  assert.equal(formatCell("   ").cls, "cell-empty");
});

test("numbers by kind, including decimals and big integers sent as strings", () => {
  assert.equal(formatCell("48000.00", "decimal").cls, "cell-num");
  assert.equal(formatCell("48000.00", "decimal").text, "48000.00", "no rounding / reformatting");
  assert.equal(formatCell("9007199254740993", "integer").text, "9007199254740993");
  assert.equal(formatCell("NaN", "float").cls, "cell-num cell-special");
});

test("dates, binary, json and multi-line text", () => {
  assert.equal(formatCell("2020-01-01T09:30:00", "datetime").text, "2020-01-01 09:30:00");
  assert.equal(formatCell("2020-01-01", "date").cls, "cell-date");
  assert.deepEqual(formatCell("AAEC/w==", "binary"), { text: "0x000102FF", cls: "cell-binary", title: "4 bytes" });
  assert.equal(formatCell({ a: 1 }, "json").text, '{"a":1}');
  const multi = formatCell("line 1\nline 2", "text");
  assert.equal(multi.text, "line 1 ↵ line 2");
  assert.equal(multi.title, "line 1\nline 2");
});

test("cell HTML is escaped", () => {
  assert.equal(cellHtml("<b>x</b>", "text"), '<span class="cell-text">&lt;b&gt;x&lt;/b&gt;</span>');
  assert.equal(cellHtml(null, "text"), '<span class="cell-null">NULL</span>');
});

test("sorting: NULL first, then empty, numbers numerically even as strings", () => {
  const vals = ["9.5", null, "48000.00", "100", null];
  assert.deepEqual([...vals].sort((a, b) => compareCells(a, b, "decimal")), [null, null, "9.5", "100", "48000.00"]);
  const big = ["9007199254740993", "9007199254740992", "-5"];
  assert.deepEqual([...big].sort((a, b) => compareCells(a, b, "integer")), ["-5", "9007199254740992", "9007199254740993"]);
  assert.deepEqual(["b", "", null, "a"].sort((a, b) => compareCells(a, b, "text")), [null, "", "a", "b"]);
  assert.deepEqual(["item10", "item2"].sort((a, b) => compareCells(a, b, "text")), ["item2", "item10"]);
  assert.deepEqual([true, false, null].sort((a, b) => compareCells(a, b, "boolean")), [null, false, true]);
});

test("column width estimate is bounded and content-aware", () => {
  const narrow = estimateColumnWidth("Id", [1, 2, 3], "integer");
  const wide = estimateColumnWidth("Email", ["bill.porter@gmail.com"], "text");
  const huge = estimateColumnWidth("Notes", ["x".repeat(5000)], "text");
  assert.ok(narrow >= 72 && narrow < wide, `${narrow} < ${wide}`);
  assert.equal(huge, 380, "capped");
});

test("CSV export keeps NULL as an empty field and real values intact", () => {
  assert.deepEqual([null, "", 0, false, { a: 1 }].map(csvValue), ["", "", 0, false, '{"a":1}']);
});

test("CSV: NULL is an empty unquoted field, '' is a quoted empty field", () => {
  const csv = toCsv(["id", "middle", "note"], [[1, null, 'say "hi", ok'], [2, "", "  pad"], [3, 0, false]]);
  assert.equal(
    csv,
    '\uFEFFid,middle,note\r\n1,,"say ""hi"", ok"\r\n2,"","  pad"\r\n3,0,false\r\n'
  );
});
