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

test("root URL (/) and /playground open publicly without authentication", () => {
  const appRoutes = [
    { path: "/", view: "landing-view" },
    { path: "/playground", view: "playground-view" },
    { path: "/login", view: "login-view" },
    { path: "/workspace", view: "dashboard-view", requiresAuth: true },
    { path: "/editor", view: "editor-view", requiresAuth: true },
  ];

  // Unauthenticated visit to "/" renders landing-view instead of redirecting to /login
  const winRoot = fakeWindow("#/");
  let resolved = null;
  const routerRoot = new Router({
    routes: appRoutes,
    authHome: "/workspace",
    guard: () => ({ authenticated: false }),
    onNavigate: (c) => (resolved = c),
    window: winRoot,
  });
  routerRoot.start();
  assert.equal(winRoot.location.hash, "#/");
  assert.equal(resolved.status, "ok");
  assert.equal(resolved.route.view, "landing-view");

  // Navigating to /playground works without authentication
  routerRoot.navigate("/playground");
  routerRoot.resolve();
  assert.equal(winRoot.location.hash, "#/playground");
  assert.equal(resolved.status, "ok");
  assert.equal(resolved.route.view, "playground-view");

  // Navigating to /login renders login-view for unauthenticated users
  routerRoot.navigate("/login");
  routerRoot.resolve();
  assert.equal(winRoot.location.hash, "#/login");
  assert.equal(resolved.status, "ok");
  assert.equal(resolved.route.view, "login-view");
});

test("protected enterprise routes redirect unauthenticated users to /login and restore destination after login", () => {
  const appRoutes = [
    { path: "/", view: "landing-view" },
    { path: "/playground", view: "playground-view" },
    { path: "/login", view: "login-view" },
    { path: "/workspace", view: "dashboard-view", requiresAuth: true },
    { path: "/editor", view: "editor-view", requiresAuth: true },
  ];

  let isAuthed = false;
  const win = fakeWindow("#/editor");
  const router = new Router({
    routes: appRoutes,
    authHome: "/workspace",
    guard: () => ({ authenticated: isAuthed, role: isAuthed ? "admin" : undefined }),
    onNavigate: () => {},
    window: win,
  });

  // Unauthenticated direct access to /editor redirects to /login
  router.start();
  assert.equal(win.location.hash, "#/login");

  // Once authenticated on /login, resolve() redirects back to the requested /editor route
  isAuthed = true;
  router.resolve();
  assert.equal(win.location.hash, "#/editor");
});

test("authenticated user visiting /login directly is redirected to /workspace", () => {
  const appRoutes = [
    { path: "/", view: "landing-view" },
    { path: "/playground", view: "playground-view" },
    { path: "/login", view: "login-view" },
    { path: "/workspace", view: "dashboard-view", requiresAuth: true },
  ];

  const win = fakeWindow("#/login");
  const router = new Router({
    routes: appRoutes,
    authHome: "/workspace",
    guard: () => ({ authenticated: true, role: "admin" }),
    onNavigate: () => {},
    window: win,
  });
  router.start();
  assert.equal(win.location.hash, "#/workspace");
});

