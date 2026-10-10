// Public Landing Page ("/"): Entry-point experience presenting two distinct environments:
//   1. SQL Playground ("/playground") — browser-based SQLite sandbox (no login required).
//   2. Enterprise SQL Workspace ("/login") — authenticated multi-DBMS administration portal.

import { app } from "../../core/context.js";
import { config } from "../../core/config.js";
import { icons } from "../../components/icons.js";
import { escapeHtml } from "../../components/view-helpers.js";
import { getTheme, toggleTheme } from "../../core/theme.js";

export class LandingView extends HTMLElement {
  connectedCallback() {
    this._render();
  }

  _render() {
    const isDark = getTheme() === "dark";
    const isAuthed = Boolean(app.auth?.isAuthenticated?.());

    this.innerHTML = `
      <div class="landing-screen">
        <header class="landing-topbar">
          <div class="landing-brand">
            <span class="dot"></span>
            <span class="landing-brand-name">${escapeHtml(config.appName)}</span>
            <span class="badge landing-brand-badge">SQL Studio</span>
          </div>
          <span class="spacer"></span>
          <button
            type="button"
            class="btn btn-ghost landing-theme-btn"
            id="landing-theme-toggle"
            title="Switch to ${isDark ? "light" : "dark"} theme"
            aria-label="Toggle color theme"
          >
            ${isDark ? icons.sun : icons.moon}
            <span class="landing-theme-label">${isDark ? "Light" : "Dark"}</span>
          </button>
        </header>

        <main class="landing-main">
          <section class="landing-hero" aria-labelledby="landing-title">
            <div class="landing-eyebrow">
              <span class="badge cat-read">SQL Environment Portal</span>
            </div>
            <h1 id="landing-title" class="landing-heading">SQL Studio</h1>
            <p class="landing-subtitle">
              Your starting point for SQL development. Practice SQL in a browser-based sandbox or
              access your enterprise database workspace.
            </p>
          </section>

          <section class="landing-cards" aria-label="Choose your SQL environment">
            <!-- Option A: SQL Playground -->
            <article
              class="landing-card landing-card-playground"
              id="card-playground"
              data-destination="/playground"
              role="link"
              tabindex="0"
              aria-label="SQL Playground — Open SQL Playground"
            >
              <div class="landing-card-top">
                <div class="landing-card-icon playground-icon" aria-hidden="true">
                  ${icons.terminal}
                </div>
                <span class="landing-pill playground-pill">
                  Free practice environment &middot; No login required
                </span>
              </div>

              <div class="landing-card-body">
                <h2 class="landing-card-title">SQL Playground</h2>
                <p class="landing-card-lead">
                  Practice SQL instantly in your browser with a safe, sample SQLite database. No
                  login or database server required.
                </p>
                <p class="landing-card-desc">
                  Practice SQL using SQLite in your browser. Explore sample tables, run queries,
                  and see results instantly without signing in.
                </p>

                <ul class="landing-features" aria-label="SQL Playground highlights">
                  <li>
                    <span class="feature-dot"></span>
                    <span>Preloaded <code>course</code>, <code>student</code>, and <code>admission</code> tables</span>
                  </li>
                  <li>
                    <span class="feature-dot"></span>
                    <span>Real in-browser SQLite engine (<code>SELECT</code>, <code>JOIN</code>, <code>INSERT</code>, <code>UPDATE</code>, <code>DELETE</code>)</span>
                  </li>
                  <li>
                    <span class="feature-dot"></span>
                    <span>One-click database reset &amp; isolated client-side execution</span>
                  </li>
                </ul>
              </div>

              <div class="landing-card-footer">
                <span class="btn btn-primary landing-cta" id="cta-playground">
                  <span>Open SQL Playground</span>
                  <span class="cta-arrow" aria-hidden="true">${icons.arrowRight}</span>
                </span>
              </div>
            </article>

            <!-- Option B: Enterprise SQL Workspace -->
            <article
              class="landing-card landing-card-enterprise"
              id="card-enterprise"
              data-destination="/login"
              role="link"
              tabindex="0"
              aria-label="Enterprise SQL Workspace — Sign In to Workspace"
            >
              <div class="landing-card-top">
                <div class="landing-card-icon enterprise-icon" aria-hidden="true">
                  ${icons.database}
                </div>
                <span class="landing-pill enterprise-pill">
                  Authenticated access &middot; Enterprise database tools
                </span>
              </div>

              <div class="landing-card-body">
                <h2 class="landing-card-title">Enterprise SQL Workspace</h2>
                <p class="landing-card-lead">
                  Connect to your database servers, explore schemas, write SQL queries, and manage
                  query execution in a powerful enterprise workspace.
                </p>
                <p class="landing-card-desc">
                  Connect to SQL Server, PostgreSQL, MySQL, and other supported database systems.
                  Execute queries and work with your existing enterprise tools.
                </p>

                <ul class="landing-features" aria-label="Enterprise SQL Workspace highlights">
                  <li>
                    <span class="feature-dot enterprise-dot"></span>
                    <span>Multi-DBMS connectivity for <strong>SQL Server</strong>, <strong>PostgreSQL</strong>, and <strong>MySQL</strong></span>
                  </li>
                  <li>
                    <span class="feature-dot enterprise-dot"></span>
                    <span>Schema Explorer, ER Diagrams, Data Viewer, and multi-statement SQL Editor</span>
                  </li>
                  <li>
                    <span class="feature-dot enterprise-dot"></span>
                    <span>Role-based access control (RBAC), encrypted credentials &amp; audit logging</span>
                  </li>
                </ul>
              </div>

              <div class="landing-card-footer">
                <span class="btn landing-cta landing-cta-enterprise" id="cta-enterprise">
                  <span>${isAuthed ? "Sign In to Workspace (Active Session)" : "Sign In to Workspace"}</span>
                  <span class="cta-arrow" aria-hidden="true">${icons.arrowRight}</span>
                </span>
              </div>
            </article>
          </section>
        </main>

        <footer class="landing-footer">
          <span>${escapeHtml(config.appName)} &middot; SQL Studio</span>
          <span class="landing-footer-sep">&middot;</span>
          <span>Choose the browser-based SQLite Playground for instant practice or sign in to the Enterprise SQL Workspace.</span>
        </footer>
      </div>
    `;

    // Theme toggle button (separate from the cards).
    const themeBtn = this.querySelector("#landing-theme-toggle");
    themeBtn?.addEventListener("click", () => {
      const dark = toggleTheme() === "dark";
      themeBtn.title = `Switch to ${dark ? "light" : "dark"} theme`;
      themeBtn.innerHTML = `${dark ? icons.sun : icons.moon}<span class="landing-theme-label">${
        dark ? "Light" : "Dark"
      }</span>`;
    });

    // Single unified navigation handler per card (prevents nested clickable element conflicts).
    this.querySelectorAll(".landing-card[data-destination]").forEach((card) => {
      const dest = card.dataset.destination;
      const activate = (e) => {
        e.preventDefault();
        app.router?.navigate(dest);
      };
      card.addEventListener("click", activate);
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          activate(e);
        }
      });
    });
  }
}

if (typeof customElements !== "undefined" && !customElements.get("landing-view")) {
  customElements.define("landing-view", LandingView);
}

