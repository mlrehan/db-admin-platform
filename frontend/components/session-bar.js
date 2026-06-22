// A toolbar that selects the active live session, or opens a new one from a saved connection.
// Updates the shared sessionStore so all data views stay in sync.

import { app } from "../core/context.js";
import { bus, Events } from "../core/events.js";
import { sessionStore, setActiveSession, setActiveDatabase } from "../core/session-state.js";
import { escapeHtml } from "./view-helpers.js";

export class SessionBar extends HTMLElement {
  async connectedCallback() {
    this.classList.add("session-bar");
    await this.refresh();
  }

  async refresh() {
    let sessions = [];
    let connections = [];
    try {
      [sessions, connections] = await Promise.all([
        app.api.listSessions(),
        app.api.listConnections(),
      ]);
    } catch {
      /* render what we have */
    }
    this._sessions = sessions;
    this._connections = connections;
    const active = sessionStore.getState().sessionId;

    const sessionOpts = sessions
      .map(
        (s) =>
          `<option value="${s.id}" ${s.id === active ? "selected" : ""}>
            ${escapeHtml(s.engine)} · ${s.id.slice(0, 8)}</option>`
      )
      .join("");
    const connOpts = connections
      .map((c) => `<option value="${c.id}">${escapeHtml(c.name)} (${c.engine})</option>`)
      .join("");

    this.innerHTML = `
      <span class="muted">Session</span>
      <select class="input session-select" ${sessions.length ? "" : "disabled"}>
        ${sessionOpts || '<option value="">No open sessions</option>'}
      </select>
      <span class="muted db-label">Database</span>
      <select class="input db-select" title="Active database" disabled>
        <option value="">—</option>
      </select>
      <span class="sep"></span>
      <select class="input conn-select">
        <option value="">Open from connection…</option>${connOpts}
      </select>
      <button class="btn open-btn">Open</button>
      <button class="btn btn-ghost refresh-btn" title="Refresh">⟳</button>`;

    this.querySelector(".session-select").addEventListener("change", (e) => {
      const s = this._sessions.find((x) => x.id === e.target.value);
      if (s) {
        setActiveSession(s);
        this._loadDatabases(s.id);
      }
    });
    this.querySelector(".db-select").addEventListener("change", (e) =>
      this._switchDatabase(e.target.value)
    );
    this.querySelector(".open-btn").addEventListener("click", () => this._open());
    this.querySelector(".refresh-btn").addEventListener("click", () => this.refresh());

    // Auto-select the first session if none active.
    const current = sessionStore.getState().sessionId || (sessions[0] && sessions[0].id);
    if (!active && sessions.length) setActiveSession(sessions[0]);
    if (current) this._loadDatabases(current);
  }

  async _loadDatabases(sessionId) {
    const select = this.querySelector(".db-select");
    if (!select) return;
    try {
      const databases = await app.api.listDatabases(sessionId);
      const activeDb = sessionStore.getState().database;
      select.innerHTML = databases
        .map(
          (d) =>
            `<option value="${escapeHtml(d.name)}" ${
              d.name === activeDb || d.is_active ? "selected" : ""
            }>${escapeHtml(d.name)}</option>`
        )
        .join("");
      select.disabled = databases.length === 0;
    } catch {
      select.innerHTML = `<option value="">—</option>`;
      select.disabled = true;
    }
  }

  async _switchDatabase(database) {
    const sessionId = sessionStore.getState().sessionId;
    if (!sessionId || !database) return;
    try {
      await app.api.switchDatabase(sessionId, database);
      setActiveDatabase(database);
      bus.emit(Events.TOAST, { message: `Using database "${database}"`, kind: "success" });
    } catch (err) {
      bus.emit(Events.TOAST, { message: err?.message || "Switch failed", kind: "error" });
    }
  }

  async _open() {
    const connId = this.querySelector(".conn-select").value;
    if (!connId) return;
    try {
      const session = await app.api.openSession(connId);
      bus.emit(Events.TOAST, { message: "Session opened", kind: "success" });
      setActiveSession(session);
      await this.refresh();
    } catch (err) {
      bus.emit(Events.TOAST, {
        message: err?.message || "Failed to open session",
        kind: "error",
      });
    }
  }
}

customElements.define("session-bar", SessionBar);
