import { test } from "node:test";
import assert from "node:assert/strict";
import { HttpClient, ApiError } from "../services/http.js";

function jsonResponse(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    statusText: "",
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
    json: async () => body,
  };
}

test("attaches bearer token and parses JSON", async () => {
  let seen;
  const http = new HttpClient({
    baseUrl: "/api",
    getAccessToken: () => "tok123",
    refresh: async () => false,
    fetch: async (url, opts) => {
      seen = { url, opts };
      return jsonResponse(200, { ok: true });
    },
  });
  const result = await http.get("/me");
  assert.equal(seen.url, "/api/me");
  assert.equal(seen.opts.headers["Authorization"], "Bearer tok123");
  assert.deepEqual(result, { ok: true });
});

test("returns null for 204", async () => {
  const http = new HttpClient({
    baseUrl: "",
    getAccessToken: () => null,
    refresh: async () => false,
    fetch: async () => ({ status: 204, ok: true, text: async () => "" }),
  });
  assert.equal(await http.delete("/x"), null);
});

test("throws ApiError with envelope code on failure", async () => {
  const http = new HttpClient({
    baseUrl: "",
    getAccessToken: () => null,
    refresh: async () => false,
    fetch: async () =>
      jsonResponse(409, { error: { code: "CONFLICT", message: "dup" } }),
  });
  await assert.rejects(http.post("/x", {}), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.status, 409);
    assert.equal(err.code, "CONFLICT");
    return true;
  });
});

test("refreshes once and retries on 401, then succeeds", async () => {
  let calls = 0;
  let refreshCalls = 0;
  const http = new HttpClient({
    baseUrl: "",
    getAccessToken: () => "old",
    refresh: async () => {
      refreshCalls++;
      return true;
    },
    fetch: async () => {
      calls++;
      return calls === 1
        ? jsonResponse(401, { error: { code: "TOKEN_EXPIRED" } })
        : jsonResponse(200, { data: 1 });
    },
  });
  const result = await http.get("/secure");
  assert.equal(refreshCalls, 1);
  assert.equal(calls, 2);
  assert.deepEqual(result, { data: 1 });
});

test("does not retry infinitely; calls onUnauthorized when refresh fails", async () => {
  let unauthorized = 0;
  let calls = 0;
  const http = new HttpClient({
    baseUrl: "",
    getAccessToken: () => "old",
    refresh: async () => false,
    onUnauthorized: () => unauthorized++,
    fetch: async () => {
      calls++;
      return jsonResponse(401, { error: { code: "TOKEN_EXPIRED" } });
    },
  });
  await assert.rejects(http.get("/secure"));
  assert.equal(calls, 1);
  assert.equal(unauthorized, 1);
});

test("concurrent 401s share a single refresh (single-flight)", async () => {
  let refreshCalls = 0;
  let firstBatch = true;
  const http = new HttpClient({
    baseUrl: "",
    getAccessToken: () => "old",
    refresh: async () => {
      refreshCalls++;
      await new Promise((r) => setTimeout(r, 10));
      return true;
    },
    fetch: async () => {
      // First two calls 401, retries succeed.
      if (firstBatch) return jsonResponse(401, { error: { code: "X" } });
      return jsonResponse(200, { ok: true });
    },
  });
  const p1 = http.get("/a");
  const p2 = http.get("/b");
  setTimeout(() => (firstBatch = false), 1);
  await Promise.all([p1, p2]);
  assert.equal(refreshCalls, 1);
});
