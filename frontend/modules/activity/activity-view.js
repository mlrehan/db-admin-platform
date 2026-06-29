// "Activity" page — a user's own audit trail. Available to every authenticated user; the
// backend scopes the records (a regular user sees only their own actions, an admin sees all).

import { app } from "../../core/context.js";
import "../../components/audit-log.js";

export class ActivityView extends HTMLElement {
  connectedCallback() {
    const isAdmin = app.auth?.user?.role === "admin";
    this.innerHTML = `
      <div class="view" style="max-width:1100px">
        <div class="view-header"><h2>Activity</h2>
          <div class="muted">${
            isAdmin
              ? "All users' query activity (you're an administrator)."
              : "Your query activity — only the actions you have performed."
          }</div></div>
        <div class="panel" id="activity-panel" style="padding:0; margin-top:12px"></div>
      </div>`;
    this.querySelector("#activity-panel").appendChild(document.createElement("audit-log"));
  }
}

customElements.define("activity-view", ActivityView);
