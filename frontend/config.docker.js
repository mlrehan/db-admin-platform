// Production runtime config (installed as /config.js in the nginx image). Points library
// bases at the locally-vendored copies so the app needs no internet access, and pins the API
// base to the same origin (nginx proxies /api and /ws to the backend).
window.__APP_CONFIG__ = {
  apiBase: "/api/v1",
  monacoBase: "/vendor/monaco/min/vs",
  agGridBase: "/vendor/ag-grid",
  swalBase: "/vendor/sweetalert2",
  tomSelectBase: "/vendor/tom-select/dist",
};
