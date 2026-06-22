import { test } from "node:test";
import assert from "node:assert/strict";
import { cached, clearMetadataCache } from "../core/metadata-cache.js";

test("dedupes concurrent/repeat calls for the same key", async () => {
  clearMetadataCache();
  let calls = 0;
  const fetcher = () => {
    calls++;
    return Promise.resolve("value");
  };
  const a = cached("k", fetcher);
  const b = cached("k", fetcher);
  assert.equal(a, b); // same in-flight promise
  assert.equal(await a, "value");
  cached("k", fetcher); // still cached
  assert.equal(calls, 1);
});

test("clearMetadataCache forces a refetch", async () => {
  clearMetadataCache();
  let calls = 0;
  const fetcher = () => {
    calls++;
    return Promise.resolve(calls);
  };
  await cached("k", fetcher);
  clearMetadataCache();
  await cached("k", fetcher);
  assert.equal(calls, 2);
});

test("does not cache rejected fetches", async () => {
  clearMetadataCache();
  let calls = 0;
  const fetcher = () => {
    calls++;
    return Promise.reject(new Error("boom"));
  };
  await assert.rejects(cached("k", fetcher));
  await assert.rejects(cached("k", fetcher));
  assert.equal(calls, 2); // retried, not served from cache
});
