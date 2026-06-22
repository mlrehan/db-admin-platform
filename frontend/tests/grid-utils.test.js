import { test } from "node:test";
import assert from "node:assert/strict";
import { sortRows, paginate, pageCount } from "../components/grid-utils.js";

test("sortRows numeric ascending/descending", () => {
  const rows = [[3], [1], [2]];
  assert.deepEqual(sortRows(rows, 0, "asc"), [[1], [2], [3]]);
  assert.deepEqual(sortRows(rows, 0, "desc"), [[3], [2], [1]]);
});

test("sortRows string compare", () => {
  const rows = [["b"], ["a"], ["c"]];
  assert.deepEqual(sortRows(rows, 0, "asc"), [["a"], ["b"], ["c"]]);
});

test("sortRows puts nulls first ascending", () => {
  const rows = [[2], [null], [1]];
  assert.deepEqual(sortRows(rows, 0, "asc"), [[null], [1], [2]]);
});

test("sortRows does not mutate input", () => {
  const rows = [[2], [1]];
  sortRows(rows, 0, "asc");
  assert.deepEqual(rows, [[2], [1]]);
});

test("paginate slices a page", () => {
  const rows = [0, 1, 2, 3, 4, 5].map((n) => [n]);
  assert.deepEqual(paginate(rows, 0, 2), [[0], [1]]);
  assert.deepEqual(paginate(rows, 2, 2), [[4], [5]]);
});

test("pageCount", () => {
  assert.equal(pageCount(0, 10), 1);
  assert.equal(pageCount(10, 10), 1);
  assert.equal(pageCount(11, 10), 2);
});
