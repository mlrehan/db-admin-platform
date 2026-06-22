import { test } from "node:test";
import assert from "node:assert/strict";
import { Router, compilePath, parseQuery } from "../core/router.js";

// A minimal fake window with a settable hash and event registration.
function fakeWindow(initialHash = "#/") {
  const listeners = {};
  return {
    location: { hash: initialHash },
    addEventListener: (type, fn) => {
      (listeners[type] ??= []).push(fn);
    },
    removeEventListener: () => {},
    _fire: (type) => (listeners[type] ?? []).forEach((fn) => fn()),
  };
}

test("compilePath matches static and param segments", () => {
  const m = compilePath("/sessions/:id/tables/:table");
  assert.deepEqual(m("/sessions/42/tables/users"), { id: "42", table: "users" });
  assert.equal(m("/sessions/42"), null);
});

test("compilePath root and trailing slashes", () => {
  assert.deepEqual(compilePath("/")( "/"), {});
  assert.deepEqual(compilePath("/connections")("/connections/"), {});
});

test("parseQuery parses key/value pairs", () => {
  assert.deepEqual(parseQuery("schema=public&x=1"), { schema: "public", x: "1" });
  assert.deepEqual(parseQuery(""), {});
});

test("router resolves a matched authed route", () => {
  const win = fakeWindow("#/connections");
  let ctx = null;
  const router = new Router({
    routes: [{ path: "/connections", view: "connections-view", requiresAuth: true }],
    guard: () => ({ authenticated: true, role: "admin" }),
    onNavigate: (c) => (ctx = c),
    window: win,
  });
  router.start();
  assert.equal(ctx.status, "ok");
  assert.equal(ctx.route.view, "connections-view");
});

test("router redirects unauthenticated users to /login", () => {
  const win = fakeWindow("#/admin");
  const calls = [];
  const router = new Router({
    routes: [
      { path: "/login", view: "login-view" },
      { path: "/admin", view: "admin-view", requiresAuth: true },
    ],
    guard: () => ({ authenticated: false }),
    onNavigate: (c) => calls.push(c),
    window: win,
  });
  router.start();
  assert.equal(win.location.hash, "#/login");
});

test("router enforces role restrictions", () => {
  const win = fakeWindow("#/admin");
  let ctx = null;
  const router = new Router({
    routes: [{ path: "/admin", view: "admin-view", requiresAuth: true, roles: ["admin"] }],
    guard: () => ({ authenticated: true, role: "viewer" }),
    onNavigate: (c) => (ctx = c),
    window: win,
  });
  router.start();
  assert.equal(ctx.status, "forbidden");
});

test("router reports not_found for unknown paths", () => {
  const win = fakeWindow("#/nope");
  let ctx = null;
  const router = new Router({
    routes: [{ path: "/", view: "home" }],
    guard: () => ({ authenticated: true }),
    onNavigate: (c) => (ctx = c),
    window: win,
  });
  router.start();
  assert.equal(ctx.status, "not_found");
});

test("authenticated user on /login is redirected home", () => {
  const win = fakeWindow("#/login");
  const router = new Router({
    routes: [
      { path: "/login", view: "login-view" },
      { path: "/", view: "dashboard-view", requiresAuth: true },
    ],
    guard: () => ({ authenticated: true }),
    onNavigate: () => {},
    window: win,
  });
  router.start();
  assert.equal(win.location.hash, "#/");
});
