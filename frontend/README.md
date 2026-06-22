# Frontend — DB Admin Platform

Vanilla **ES6 modules + Web Components**. No framework, no build step (the browser loads
modules directly). Monaco (SQL editor) and AG Grid (data tables) are integrated in Phase 8.

## Layout (per architecture)

| Path | Responsibility |
|---|---|
| `core/` | Framework-free primitives: `router` (hash routing + guards), `store`, `events` (bus), `config`, `context` (service singletons) |
| `services/` | `http` (fetch + JWT + single-flight refresh-on-401), `auth` (token lifecycle), `api` (typed endpoint methods), `ws` (query streaming client) |
| `components/` | Reusable Web Components: `app-root` (shell), `ui-toast`, icons, view helpers |
| `modules/` | Feature views: `auth` (login), `connections` (dashboard + list), `editor`, `schema`, `viewer`, `admin` |
| `styles/` | `tokens.css` (design system), `base.css`, `app.css` |
| `main.js` | Bootstrap: wires services, router → shell, restores session, starts routing |

## Run locally

No build required — serve the directory over HTTP (ES modules need a server, not `file://`):

```bash
python -m http.server 5501 --directory frontend
# open http://localhost:5501
```

By default the app calls the API at same-origin `/api/v1` (nginx proxies it in Phase 9). To
point at a separately-running backend during development, define before `main.js` loads:

```html
<script>window.__APP_CONFIG__ = { apiBase: "http://localhost:8000/api/v1" };</script>
```

## Test

Core logic is dependency-injected and unit-tested with Node's built-in runner (no deps):

```bash
cd frontend
npm test          # node --test "tests/*.test.js"  → router, http, store, events, ws
npm run check     # node --check main.js
```

## Design notes
- **Auth tokens:** access token in memory; refresh token in `localStorage` so a reload can
  restore the session. A hardened deployment would move the refresh token to an httpOnly
  cookie (needs backend cookie support).
- **Routing:** hash-based (`#/connections`) — robust for static hosting with no rewrite rules.
