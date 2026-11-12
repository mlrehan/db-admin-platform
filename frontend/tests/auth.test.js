import { test } from "node:test";
import assert from "node:assert/strict";
import { AuthService } from "../services/auth.js";
import { ApiError } from "../services/http.js";

function storage(initial = {}) {
  const m = new Map(Object.entries(initial));
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k) };
}

function fakeHttp(responses) {
  const calls = [];
  return {
    calls,
    async post(path) {
      calls.push(path);
      const next = responses.shift();
      if (next instanceof Error) throw next;
      return next;
    },
    async get() {
      return { id: "u1", email: "a@b.c", role: "admin" };
    },
  };
}

test("a rejected refresh token (401) is discarded", async () => {
  const s = storage({ "dbadmin.refresh": "rt" });
  const auth = new AuthService({ http: fakeHttp([new ApiError(401, "UNAUTHORIZED", "expired")]), storage: s });
  assert.equal(await auth.refresh(), false);
  assert.equal(s.getItem("dbadmin.refresh"), null);
});

test("a network error or 5xx keeps the refresh token (no logout on a blip)", async () => {
  const s = storage({ "dbadmin.refresh": "rt" });
  const auth = new AuthService({
    http: fakeHttp([new TypeError("Failed to fetch"), new ApiError(503, "UNAVAILABLE", "down")]),
    storage: s,
  });
  assert.equal(await auth.refresh(), false);
  assert.equal(auth.lastRefreshTransient, true);
  assert.equal(await auth.refresh(), false);
  assert.equal(s.getItem("dbadmin.refresh"), "rt");
});

test("restore retries while the API is starting, then signs the user back in", async () => {
  const s = storage({ "dbadmin.refresh": "rt" });
  const http = fakeHttp([
    new TypeError("Failed to fetch"),
    new TypeError("Failed to fetch"),
    { access_token: "at", refresh_token: "rt2" },
  ]);
  const auth = new AuthService({ http, storage: s });
  assert.equal(await auth.restore({ attempts: 4, delayMs: 1 }), true);
  assert.equal(http.calls.length, 3);
  assert.equal(auth.isAuthenticated(), true);
  assert.equal(s.getItem("dbadmin.refresh"), "rt2");
});

test("restore does not retry a definitive rejection", async () => {
  const http = fakeHttp([new ApiError(401, "UNAUTHORIZED", "revoked")]);
  const auth = new AuthService({ http, storage: storage({ "dbadmin.refresh": "rt" }) });
  assert.equal(await auth.restore({ attempts: 4, delayMs: 1 }), false);
  assert.equal(http.calls.length, 1);
});
