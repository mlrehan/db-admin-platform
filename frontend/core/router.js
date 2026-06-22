// Hash-based client-side router. Hash routing is robust for static hosting (no server
// rewrite rules needed). `window` is injectable so the matching/navigation logic is
// unit-testable in Node.

export class Router {
  /**
   * @param {object} opts
   * @param {Array<{path:string, view:string, requiresAuth?:boolean, roles?:string[]}>} opts.routes
   * @param {(ctx)=>void} opts.onNavigate  Called with the resolved route + params.
   * @param {()=>({authenticated:boolean, role?:string})} opts.guard  Auth state provider.
   * @param {Window} [opts.window]
   */
  constructor({ routes, onNavigate, guard, window: win = globalThis }) {
    this.routes = routes.map((r) => ({ ...r, matcher: compilePath(r.path) }));
    this.onNavigate = onNavigate;
    this.guard = guard || (() => ({ authenticated: true }));
    this.window = win;
    this._handler = () => this.resolve();
  }

  start() {
    this.window.addEventListener("hashchange", this._handler);
    this.resolve();
  }

  stop() {
    this.window.removeEventListener("hashchange", this._handler);
  }

  navigate(path) {
    const target = path.startsWith("#") ? path : `#${path}`;
    if (this.window.location.hash === target) {
      this.resolve();
    } else {
      this.window.location.hash = target;
    }
  }

  currentPath() {
    const hash = this.window.location.hash || "#/";
    return hash.slice(1) || "/";
  }

  match(path) {
    const [rawPath, queryString] = path.split("?");
    for (const route of this.routes) {
      const params = route.matcher(rawPath);
      if (params) {
        return { route, params, query: parseQuery(queryString) };
      }
    }
    return null;
  }

  resolve() {
    const path = this.currentPath();
    const matched = this.match(path);
    const auth = this.guard();

    if (!matched) {
      this.onNavigate({ status: "not_found", path });
      return;
    }
    const { route, params, query } = matched;

    if (route.requiresAuth && !auth.authenticated) {
      this.navigate("/login");
      return;
    }
    if (route.roles && auth.role && !route.roles.includes(auth.role)) {
      this.onNavigate({ status: "forbidden", route, params, query });
      return;
    }
    // Already-authenticated users shouldn't sit on the login screen.
    if (route.path === "/login" && auth.authenticated) {
      this.navigate("/");
      return;
    }
    this.onNavigate({ status: "ok", route, params, query });
  }
}

// Compile "/sessions/:id/tables/:table" into a matcher returning params or null.
export function compilePath(pattern) {
  const keys = [];
  const regexSource = pattern
    .replace(/\/+$/, "")
    .split("/")
    .map((segment) => {
      if (segment.startsWith(":")) {
        keys.push(segment.slice(1));
        return "([^/]+)";
      }
      return segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    })
    .join("/");
  const regex = new RegExp(`^${regexSource || ""}/?$`);

  return (path) => {
    const normalized = path.replace(/\/+$/, "") || "/";
    const match = regex.exec(normalized === "" ? "/" : normalized);
    if (!match) return null;
    const params = {};
    keys.forEach((key, i) => {
      params[key] = decodeURIComponent(match[i + 1]);
    });
    return params;
  };
}

export function parseQuery(queryString) {
  const out = {};
  if (!queryString) return out;
  for (const pair of queryString.split("&")) {
    if (!pair) continue;
    const [k, v = ""] = pair.split("=");
    out[decodeURIComponent(k)] = decodeURIComponent(v);
  }
  return out;
}
