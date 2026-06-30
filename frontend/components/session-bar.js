// A toolbar that selects the active live session, or opens a new one from a saved connection.
// Updates the shared sessionStore so all data views stay in sync.

import { app } from "../core/context.js";
import { bus, Events } from "../core/events.js";
import { sessionStore, setActiveSession, setActiveDatabase } from "../core/session-state.js";
import { clearMetadataCache } from "../core/metadata-cache.js";
import { promptText } from "../core/notify.js";
import { escapeHtml } from "./view-helpers.js";

export class SessionBar extends HTMLElement {
  async connectedCallback() {
    this.classList.add("session-bar");
    // A DDL run (e.g. CREATE DATABASE) may have changed the database list — reload it so the
    // new database appears immediately.
    this._unsubMeta = bus.on(Events.METADATA_CHANGED, () => {
      const sid = sessionStore.getState().sessionId;
      if (sid) this._loadDatabases(sid);
    });
    await this.refresh();
  }

  disconnectedCallback() {
    this._unsubMeta?.();
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

    // Label a session by its connection (server) name so the user can tell which server it is —
    // e.g. "PostGreSQL_158 (postgresql)" rather than an opaque "postgresql · 1a2b3c4d".
    const connName = (id) => connections.find((c) => c.id === id)?.name;
    const sessionLabel = (s) =>
      connName(s.connection_id)
        ? `${connName(s.connection_id)} (${s.engine})`
        : `${s.engine} · ${s.id.slice(0, 8)}`;
    const sessionOpts = sessions
      .map(
        (s) =>
          `<option value="${s.id}" ${s.id === active ? "selected" : ""}>${escapeHtml(
            sessionLabel(s)
          )}</option>`
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
      <button class="btn btn-ghost newdb-btn" title="Create a new database" ${
        this._canCreateDb(active) ? "" : "hidden"
      }>＋ Database</button>
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
        this._toggleNewDbButton(s.id);
      }
    });
    this.querySelector(".db-select").addEventListener("change", (e) =>
      this._switchDatabase(e.target.value)
    );
    this.querySelector(".newdb-btn").addEventListener("click", () => this._createDatabase());
    this.querySelector(".open-btn").addEventListener("click", () => this._open());
    this.querySelector(".refresh-btn").addEventListener("click", () => this.refresh());

    // Auto-select the first session if none active.
    const current = sessionStore.getState().sessionId || (sessions[0] && sessions[0].id);
    if (!active && sessions.length) setActiveSession(sessions[0]);
    if (current) {
      this._loadDatabases(current);
      this._toggleNewDbButton(current);
    }
  }

  async _loadDatabases(sessionId) {
    const select = this.querySelector(".db-select");
    if (!select) return;
    try {
      const databases = await app.api.listDatabases(sessionId);
      const names = databases.map((d) => d.name);
      const sess = (this._sessions || []).find((s) => s.id === sessionId);
      const serverActive = sess?.active_database || "";
      const storeDb = sessionStore.getState().database;
      // Choose which database to show as active: the user's stored choice, else the server's
      // current database, else any flagged active, else the first available one.
      let desired = "";
      if (storeDb && names.includes(storeDb)) desired = storeDb;
      else if (serverActive && names.includes(serverActive)) desired = serverActive;
      else desired = databases.find((d) => d.is_active)?.name || names[0] || "";

      select.innerHTML =
        databases
          .map(
            (d) =>
              `<option value="${escapeHtml(d.name)}" ${
                d.name === desired ? "selected" : ""
              }>${escapeHtml(d.name)}</option>`
          )
          .join("") || `<option value="">—</option>`;
      select.disabled = databases.length === 0;

      // The dropdown must never lie: if what we show as selected isn't what the server is
      // actually using, switch the server to it. This is what makes a granted user's session
      // target the right database so their queries aren't wrongly denied.
      if (desired && desired !== serverActive) {
        await this._switchDatabase(desired, { silent: true });
      } else if (desired) {
        setActiveDatabase(desired);
      }
    } catch {
      select.innerHTML = `<option value="">—</option>`;
      select.disabled = true;
    }
  }

  _canCreateDb(sessionId) {
    const s = (this._sessions || []).find((x) => x.id === sessionId);
    return !!(s && s.can_create_database);
  }

  _toggleNewDbButton(sessionId) {
    const btn = this.querySelector(".newdb-btn");
    if (btn) btn.hidden = !this._canCreateDb(sessionId);
  }

  async _createDatabase() {
    const sessionId = sessionStore.getState().sessionId;
    if (!sessionId) return;
    const name = await promptText({
      title: "Create database",
      text: "Name the new database. It will be created on the connected server.",
      placeholder: "my_new_database",
      confirmText: "Create",
    });
    if (!name) return;
    try {
      const created = await app.api.createDatabase(sessionId, name);
      clearMetadataCache(); // the database list changed
      await this._loadDatabases(sessionId);
      bus.emit(Events.TOAST, {
        message: `Database "${created.name}" created`,
        kind: "success",
      });
    } catch (err) {
      bus.emit(Events.TOAST, {
        message: err?.message || "Could not create database",
        kind: "error",
      });
    }
  }

  async _switchDatabase(database, { silent = false } = {}) {
    const sessionId = sessionStore.getState().sessionId;
    if (!sessionId || !database) return;
    try {
      await app.api.switchDatabase(sessionId, database);
      setActiveDatabase(database);
      // Keep the cached session object in sync so a later reload doesn't switch again.
      const sess = (this._sessions || []).find((s) => s.id === sessionId);
      if (sess) sess.active_database = database;
      if (!silent) {
        bus.emit(Events.TOAST, { message: `Using database "${database}"`, kind: "success" });
      }
    } catch (err) {
      if (!silent) {
        bus.emit(Events.TOAST, { message: err?.message || "Switch failed", kind: "error" });
      }
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
