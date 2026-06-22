// Runtime configuration, loaded before the application modules. In local development this is
// empty (the app falls back to sensible defaults: same-origin API, CDN-hosted libraries).
//
// The production container REPLACES this file (see frontend/Dockerfile) to point library
// bases at locally-vendored copies and pin the API base, so the deployed app needs no CDN.
//
// Local dev: the SPA is served on :8080 while the backend runs on :8000, so point the API at
// the backend's origin. (Set to {} if you serve the SPA same-origin with the backend.)
window.__APP_CONFIG__ = {
  apiBase: "http://localhost:8000/api/v1",
};
