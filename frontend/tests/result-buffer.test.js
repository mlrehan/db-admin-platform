import { test } from "node:test";
import assert from "node:assert/strict";
import { ResultBuffer } from "../modules/editor/result-buffer.js";

test("accumulates a streaming SELECT", () => {
  const buf = new ResultBuffer();
  buf.handle({ type: "accepted", query_id: "q1", category: "read" });
  assert.equal(buf.status, "running");
  buf.handle({ type: "columns", columns: [{ name: "id" }, { name: "label" }], returns_rows: true });
  buf.handle({ type: "rows", rows: [[1, "a"], [2, "b"]] });
  buf.handle({ type: "rows", rows: [[3, "c"]] });
  buf.handle({ type: "end", row_count: 3, category: "read", destructive: false });

  assert.equal(buf.status, "done");
  assert.equal(buf.rowCount, 3);
  assert.equal(buf.rows.length, 3);
  assert.deepEqual(buf.columns.map((c) => c.name), ["id", "label"]);
  assert.ok(buf.isTerminal);
});

test("captures rows_affected for writes", () => {
  const buf = new ResultBuffer();
  buf.handle({ type: "accepted", query_id: "q2", category: "write" });
  buf.handle({ type: "columns", columns: [], returns_rows: false });
  buf.handle({ type: "end", rows_affected: 7 });
  assert.equal(buf.rowsAffected, 7);
  assert.equal(buf.returnsRows, false);
});

test("error event sets error state", () => {
  const buf = new ResultBuffer();
  buf.handle({ type: "error", code: "QUERY_EXECUTION_ERROR", message: "syntax error" });
  assert.equal(buf.status, "error");
  assert.equal(buf.error, "syntax error");
  assert.ok(buf.isTerminal);
});

test("cancelled event", () => {
  const buf = new ResultBuffer();
  buf.handle({ type: "accepted", query_id: "q3" });
  buf.handle({ type: "cancelled" });
  assert.equal(buf.status, "cancelled");
});

test("reset clears state", () => {
  const buf = new ResultBuffer();
  buf.handle({ type: "rows", rows: [[1]] });
  buf.reset();
  assert.equal(buf.rows.length, 0);
  assert.equal(buf.status, "idle");
});
