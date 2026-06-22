// Dashboard / home view. Exercises the API client end-to-end: shows the signed-in user and
// live counts of saved connections and open sessions. Degrades gracefully on error.

import { app } from "../../core/context.js";
import { escapeHtml } from "../../components/view-helpers.js";
import { openHelp } from "../auth/help.js";

const ONBOARD_KEY = "dbadmin.onboarded";

export class DashboardView extends HTMLElement {
  async connectedCallback() {
    const user = app.auth?.user;
    let dismissed = true;
    try {
      dismissed = localStorage.getItem(ONBOARD_KEY) === "1";
    } catch {
      /* ignore */
    }

    const onboarding = dismissed
      ? ""
      : `<div class="panel onboard-panel">
          <div>
            <h3 style="margin:0 0 4px">👋 Welcome to ${escapeHtml(app.api ? "DB Admin Platform" : "")}</h3>
            <div class="muted">New here? Connect a database, run your first query, and explore your schema.</div>
          </div>
          <span class="spacer"></span>
          <button class="btn btn-primary" id="ob-start">Getting started</button>
          <button class="btn btn-ghost" id="ob-dismiss">Dismiss</button>
        </div>`;

    this.innerHTML = `
      <div class="view">
        <div class="view-header">
          <h2>Overview</h2>
          <div class="muted">Signed in as
            <strong>${escapeHtml(user?.email ?? "")}</strong>
            <span class="badge">${escapeHtml(user?.role ?? "")}</span>
          </div>
        </div>
        ${onboarding}
        <div class="panel stats-panel">
          <div><div class="muted">Connections</div>
            <div id="c" style="font-size:28px;font-weight:700">—</div></div>
          <div><div class="muted">Open sessions</div>
            <div id="s" style="font-size:28px;font-weight:700">—</div></div>
        </div>
      </div>`;

    this.querySelector("#ob-start")?.addEventListener("click", () => openHelp());
    this.querySelector("#ob-dismiss")?.addEventListener("click", () => {
      try {
        localStorage.setItem(ONBOARD_KEY, "1");
      } catch {
        /* ignore */
      }
      this.querySelector(".onboard-panel")?.remove();
    });

    try {
      const [connections, sessions] = await Promise.all([
        app.api.listConnections(),
        app.api.listSessions(),
      ]);
      this.querySelector("#c").textContent = connections.length;
      this.querySelector("#s").textContent = sessions.length;
    } catch {
      this.querySelector("#c").textContent = "0";
      this.querySelector("#s").textContent = "0";
    }
  }
}

customElements.define("dashboard-view", DashboardView);
