// Application bootstrap: construct the service singletons, wire the router to the shell,
// attempt to restore a session, then start routing.

import { config } from "./core/config.js";
import { app } from "./core/context.js";
import { Router } from "./core/router.js";
import { bus, Events } from "./core/events.js";
import { HttpClient } from "./services/http.js";
import { AuthService } from "./services/auth.js";
import { Api } from "./services/api.js";

// Eagerly register only what's needed for first paint (shell + login + dashboard).
import "./components/ui-toast.js";
import "./components/app-root.js";
import { initTheme } from "./core/theme.js";
import { initNotify } from "./core/notify.js";
import "./modules/auth/login-view.js";
import "./modules/connections/dashboard-view.js";

// Heavier feature views (and their Monaco/AG Grid/WebSocket deps) are loaded on demand the
// first time their route is visited — keeping the initial load small and fast.
const ROUTES = [
  { path: "/login", view: "login-view" },
  { path: "/", view: "dashboard-view", requiresAuth: true },
  {
    path: "/connections", view: "connections-view", requiresAuth: true,
    load: () => import("./modules/connections/connections-view.js"),
  },
  {
    path: "/editor", view: "editor-view", requiresAuth: true,
    load: () => import("./modules/editor/editor-view.js"),
  },
  {
    path: "/schema", view: "schema-view", requiresAuth: true,
    load: () => import("./modules/schema/schema-view.js"),
  },
  {
    path: "/data", view: "viewer-view", requiresAuth: true,
    load: () => import("./modules/viewer/viewer-view.js"),
  },
  {
    path: "/admin", view: "admin-view", requiresAuth: true, roles: ["admin"],
    load: () => import("./modules/admin/admin-view.js"),
  },
];

async function bootstrap() {
  initTheme();
  initNotify();
  // HTTP client wired to auth for token attach + refresh-and-retry on 401.
  const http = new HttpClient({
    baseUrl: config.apiBase,
    getAccessToken: () => app.auth?.getAccessToken() ?? null,
    refresh: () => app.auth?.refresh() ?? Promise.resolve(false),
    onUnauthorized: () => {
      app.auth?.clear();
      bus.emit(Events.AUTH_CHANGED, { authenticated: false });
      app.router?.navigate("/login");
    },
  });

  app.http = http;
  app.auth = new AuthService({ http });
  app.api = new Api(http);

  const root = document.createElement("app-root");
  document.getElementById("app").appendChild(root);
  document.body.appendChild(document.createElement("ui-toast"));

  app.router = new Router({
    routes: ROUTES,
    guard: () => ({
      authenticated: app.auth.isAuthenticated(),
      role: app.auth.user?.role,
    }),
    onNavigate: (ctx) => root.renderRoute(ctx),
  });

  // Surface API errors as toasts globally.
  bus.on(Events.UNAUTHORIZED, () => app.router.navigate("/login"));

  // Try to restore a prior session before the first route resolves.
  await app.auth.restore();
  app.router.start();
}

bootstrap().catch((err) => {
  console.error("Bootstrap failed", err);
  document.getElementById("app").innerHTML =
    '<div style="padding:40px;color:#e5534b">Failed to start the application.</div>';
});
