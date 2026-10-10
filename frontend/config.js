// Runtime configuration, loaded before the application modules. In local development this is
// empty (the app falls back to sensible defaults: same-origin API, CDN-hosted libraries).
//
// The production container REPLACES this file (see frontend/Dockerfile) to point library
// bases at locally-vendored copies and pin the API base, so the deployed app needs no CDN.
//
// Local dev: the SPA is served on :18480 while the backend runs on :18400, so point the API at
// the backend's origin. (Set to {} if you serve the SPA same-origin with the backend.)
window.__APP_CONFIG__ = {
  apiBase: "http://localhost:18400/api/v1",
};
