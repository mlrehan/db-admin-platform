// Runtime configuration. Values can be overridden at deploy time by defining
// `window.__APP_CONFIG__` (e.g. injected by nginx) before the app bootstraps.

const overrides = (typeof window !== "undefined" && window.__APP_CONFIG__) || {};

export const config = {
  // Base URL of the backend API. Defaults to same-origin (nginx proxies /api in Phase 9).
  apiBase: overrides.apiBase || "/api/v1",
  // WebSocket base. Derived from apiBase against the current origin when same-origin.
  wsBase: overrides.wsBase || null,
  appName: overrides.appName || "DB Admin Platform",
  // localStorage key for the refresh token.
  refreshTokenKey: "dbadmin.refresh",
  // Third-party library bases. Default to CDN for zero-setup local dev; the production
  // container overrides these (via /config.js) to point at locally-vendored copies so the
  // app runs fully offline / air-gapped.
  monacoBase: overrides.monacoBase || "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs",
  agGridBase: overrides.agGridBase || "https://cdn.jsdelivr.net/npm/ag-grid-community@31.3.4",
  swalBase: overrides.swalBase || "https://cdn.jsdelivr.net/npm/sweetalert2@11",
};

// Resolve an absolute ws:// or wss:// URL for a given API path.
export function resolveWsUrl(path) {
  if (config.wsBase) return config.wsBase + path;
  if (typeof window === "undefined") return path;
  const base = config.apiBase.startsWith("http")
    ? new URL(config.apiBase)
    : new URL(config.apiBase, window.location.origin);
  const proto = base.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${base.host}${base.pathname.replace(/\/$/, "")}${path}`;
}
