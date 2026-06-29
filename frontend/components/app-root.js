// Top-level application shell. Renders either the full-screen login view or the
// authenticated layout (brand, topbar, icon sidebar, routed main outlet). The router drives
// it via `renderRoute()`; auth changes re-resolve the current route.

import { app } from "../core/context.js";
import { config } from "../core/config.js";
import { bus, Events } from "../core/events.js";
import { icons } from "./icons.js";
import { escapeHtml } from "./view-helpers.js";
import { openChangePassword } from "../modules/auth/change-password.js";
import { openHelp } from "../modules/auth/help.js";
import { openSessions } from "../modules/sessions/sessions.js";
import { getTheme, toggleTheme } from "../core/theme.js";

const NAV = [
  { path: "/", icon: "home", label: "Overview", view: "dashboard-view" },
  { path: "/connections", icon: "database", label: "Connections", view: "connections-view" },
  { path: "/editor", icon: "editor", label: "SQL Editor", view: "editor-view" },
  { path: "/schema", icon: "schema", label: "Schema Explorer", view: "schema-view" },
  { path: "/data", icon: "table", label: "Data Viewer", view: "viewer-view" },
  { path: "/diagram", icon: "diagram", label: "Diagram", view: "diagram-view" },
  { path: "/activity", icon: "activity", label: "Activity", view: "activity-view" },
  { path: "/admin", icon: "shield", label: "Admin", view: "admin-view", roles: ["admin"] },
];

export class AppRoot extends HTMLElement {
  connectedCallback() {
    this._mode = null; // "login" | "shell"
    this._unsub = bus.on(Events.AUTH_CHANGED, () => app.router?.resolve());
  }

  disconnectedCallback() {
    this._unsub?.();
  }

  async renderRoute(ctx) {
    if (ctx.status === "not_found" || ctx.status === "forbidden") {
      this._ensureShellOrBare();
      this._setOutlet(this._messageView(ctx.status));
      return;
    }
    const { route } = ctx;
    if (route.path === "/login") {
      this._renderLogin();
      return;
    }
    this._renderShell();
    this._setActiveNav(route.path);
    this._setTitle(NAV.find((n) => n.path === route.path)?.label || config.appName);
    // Lazily import the view module on first visit (defers Monaco/AG Grid/etc.).
    if (route.load) {
      try {
        await route.load();
      } catch (err) {
        console.error("Failed to load view", route.view, err);
      }
    }
    this._setOutlet(document.createElement(route.view));
  }

  // --- login mode ----------------------------------------------------------------------

  _renderLogin() {
    if (this._mode === "login") return;
    this._mode = "login";
    this.innerHTML = "";
    this.appendChild(document.createElement("login-view"));
  }

  // --- shell mode ----------------------------------------------------------------------

  _renderShell() {
    if (this._mode === "shell") return;
    this._mode = "shell";
    const role = app.auth?.user?.role;
    const navHtml = NAV.filter((n) => !n.roles || n.roles.includes(role))
      .map(
        (n) => `
        <div class="nav-item" data-path="${n.path}" title="${n.label}"
             role="button" tabindex="0">${icons[n.icon]}</div>`
      )
      .join("");

    const email = app.auth?.user?.email || "";
    const initial = (email || "?")[0].toUpperCase();
    this.innerHTML = `
      <div class="app-shell">
        <div class="app-brand" title="${escapeHtml(config.appName)}">DB</div>
        <header class="app-topbar">
          <button class="btn btn-ghost sidebar-toggle" id="sidebar-toggle" title="Menu"
            style="padding:6px">${icons.menu}</button>
          <span class="title" id="section-title"></span>
          <span class="spacer"></span>
          <button class="btn btn-ghost" id="help" title="Help & guide"
            style="padding:6px">${icons.help}</button>
          <button class="btn btn-ghost" id="theme-toggle" title="Toggle theme"
            style="padding:6px">${getTheme() === "dark" ? icons.sun : icons.moon}</button>
          <div class="user-dropdown">
            <button class="user-trigger" id="user-trigger" aria-haspopup="true" aria-expanded="false">
              <div class="avatar">${escapeHtml(initial)}</div>
              <div class="user-meta">
                <div class="user-email">${escapeHtml(email)}</div>
                <div class="muted user-role">${escapeHtml(app.auth?.user?.role || "")}</div>
              </div>
              <span class="caret">▾</span>
            </button>
            <div class="user-menu-panel hidden" id="user-panel" role="menu">
              <div class="menu-header">
                <div class="avatar lg">${escapeHtml(initial)}</div>
                <div>
                  <div class="menu-name">${escapeHtml(email)}</div>
                  <div class="muted" style="font-size:var(--fs-xs)">Role: ${escapeHtml(app.auth?.user?.role || "")}</div>
                </div>
              </div>
              <button class="menu-item" id="m-sessions">${icons.database}<span>Active sessions</span></button>
              <button class="menu-item" id="m-change-pw">${icons.key}<span>Change password</span></button>
              <button class="menu-item" id="m-theme">${getTheme() === "dark" ? icons.sun : icons.moon}<span>Switch to ${getTheme() === "dark" ? "light" : "dark"} theme</span></button>
              <button class="menu-item" id="m-help">${icons.help}<span>Help &amp; guide</span></button>
              <div class="menu-sep"></div>
              <button class="menu-item danger" id="m-logout">${icons.logout}<span>Sign out</span></button>
            </div>
          </div>
        </header>
        <nav class="app-sidebar" id="app-sidebar-nav">${navHtml}</nav>
        <div class="nav-scrim" id="nav-scrim" aria-hidden="true"></div>
        <main class="app-main" id="outlet"></main>
      </div>`;

    this._outlet = this.querySelector("#outlet");
    const shell = this.querySelector(".app-shell");
    const toggle = this.querySelector("#sidebar-toggle");
    const closeNav = () => {
      shell.classList.remove("nav-open");
      toggle.setAttribute("aria-expanded", "false");
    };
    this.querySelectorAll(".nav-item").forEach((el) => {
      const go = () => {
        app.router.navigate(el.dataset.path);
        closeNav();
      };
      el.addEventListener("click", go);
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") go();
      });
    });
    // Mobile drawer toggle.
    toggle.setAttribute("aria-controls", "app-sidebar-nav");
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = shell.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    // Tap the dimmed backdrop (or anywhere in the content) to close the drawer.
    this.querySelector("#nav-scrim").addEventListener("click", closeNav);
    this._outlet.addEventListener("click", closeNav);
    // Esc closes the drawer.
    this.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeNav();
    });

    // Topbar quick actions.
    this.querySelector("#help").addEventListener("click", () => openHelp());
    const themeBtn = this.querySelector("#theme-toggle");
    themeBtn.addEventListener("click", () => this._toggleTheme());

    // User dropdown.
    const trigger = this.querySelector("#user-trigger");
    const panel = this.querySelector("#user-panel");
    const closePanel = () => {
      panel.classList.add("hidden");
      trigger.setAttribute("aria-expanded", "false");
    };
    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = panel.classList.toggle("hidden");
      trigger.setAttribute("aria-expanded", String(!open));
    });
    document.addEventListener("click", closePanel);
    this.querySelector("#m-sessions").addEventListener("click", () => {
      closePanel();
      openSessions();
    });
    this.querySelector("#m-change-pw").addEventListener("click", () => {
      closePanel();
      openChangePassword();
    });
    this.querySelector("#m-theme").addEventListener("click", () => {
      closePanel();
      this._toggleTheme();
    });
    this.querySelector("#m-help").addEventListener("click", () => {
      closePanel();
      openHelp();
    });
    this.querySelector("#m-logout").addEventListener("click", async () => {
      closePanel();
      await app.auth.logout();
      app.router.navigate("/login");
    });
  }

  _toggleTheme() {
    const dark = toggleTheme() === "dark";
    const themeBtn = this.querySelector("#theme-toggle");
    if (themeBtn) themeBtn.innerHTML = dark ? icons.sun : icons.moon;
    // Keep the dropdown label in sync ("Switch to light/dark theme").
    const item = this.querySelector("#m-theme");
    if (item) {
      item.innerHTML = `${dark ? icons.sun : icons.moon}<span>Switch to ${
        dark ? "light" : "dark"
      } theme</span>`;
    }
  }

  _ensureShellOrBare() {
    if (app.auth?.isAuthenticated()) {
      this._renderShell();
    } else if (this._mode !== "shell") {
      this._mode = "bare";
      this.innerHTML = `<main class="app-main">${""}</main>`;
      this._outlet = this.querySelector("main");
    }
  }

  _setActiveNav(path) {
    this.querySelectorAll(".nav-item").forEach((el) =>
      el.classList.toggle("active", el.dataset.path === path)
    );
  }

  _setTitle(text) {
    const el = this.querySelector("#section-title");
    if (el) el.textContent = text;
  }

  _setOutlet(node) {
    if (!this._outlet) return;
    this._outlet.replaceChildren(node);
  }

  _messageView(kind) {
    const el = document.createElement("div");
    el.className = "view";
    const msg =
      kind === "forbidden"
        ? "You don’t have access to this section."
        : "Page not found.";
    el.innerHTML = `<div class="panel"><div class="placeholder">
      <div class="ph-icon">∅</div><div>${msg}</div></div></div>`;
    return el;
  }
}

customElements.define("app-root", AppRoot);
