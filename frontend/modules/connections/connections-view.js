// Connection Manager: list, create, edit, test, open-session and delete saved connections.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { openModal } from "../../components/modal.js";
import { confirm, loading, alertError } from "../../core/notify.js";
import { setActiveSession } from "../../core/session-state.js";
import { escapeHtml } from "../../components/view-helpers.js";

const ENGINES = [
  { value: "postgresql", label: "PostgreSQL", port: 5432 },
  { value: "mysql", label: "MySQL", port: 3306 },
  { value: "mssql", label: "SQL Server", port: 1433 },
  // SQLite databases are files in the server's data directory: no host, port or login.
  { value: "sqlite", label: "SQLite (database files on the server)", port: 0, fileBased: true },
];

const isFileBased = (engine) => !!ENGINES.find((e) => e.value === engine)?.fileBased;

export class ConnectionsView extends HTMLElement {
  async connectedCallback() {
    this._userId = app.auth?.user?.id ?? null;
    // Only administrators may create/manage connections.
    this._isAdmin = app.auth?.user?.role === "admin";
    this.innerHTML = `
      <div class="view" style="max-width:1000px">
        <div class="view-header row">
          <div>
            <h2>Connections</h2>
            <div class="muted">${
              this._isAdmin
                ? "Register and manage target database connections, and share them with users."
                : "Database connections an administrator has given you access to."
            }</div>
          </div>
          <span class="spacer"></span>
          ${this._isAdmin ? '<button class="btn btn-primary" id="new">+ New connection</button>' : ""}
        </div>
        <div class="panel" id="list" style="padding:0"></div>
      </div>`;
    this.querySelector("#new")?.addEventListener("click", () => this._openForm());
    await this._load();
  }

  async _load() {
    const list = this.querySelector("#list");
    try {
      const conns = await app.api.listConnections();
      if (!conns.length) {
        list.innerHTML = `<div class="placeholder"><div class="ph-icon">⛁</div>
          <div>${
            this._isAdmin
              ? "No connections yet. Create your first one."
              : "No connections have been shared with you yet. Ask an administrator for access."
          }</div></div>`;
        return;
      }
      list.innerHTML = `
        <div class="table-scroll">
          <table class="grid-table conn-table">
            <thead><tr><th>Name</th><th>Engine</th><th>Host</th><th>Database</th>
              <th style="text-align:right">Actions</th></tr></thead>
            <tbody>${conns.map((c) => this._row(c)).join("")}</tbody>
          </table>
        </div>`;
      list.querySelectorAll("[data-action]").forEach((btn) =>
        btn.addEventListener("click", () =>
          this._action(btn.dataset.action, conns.find((c) => c.id === btn.dataset.id))
        )
      );
    } catch (err) {
      list.innerHTML = `<div class="placeholder">${escapeHtml(err.message)}</div>`;
    }
  }

  _row(c) {
    const mine = !this._userId || String(c.owner_id) === String(this._userId);
    const ownerActions = mine
      ? `<button class="btn btn-ghost" data-action="test" data-id="${c.id}">Test</button>
         <button class="btn" data-action="open" data-id="${c.id}">Connect</button>
         <button class="btn btn-ghost" data-action="edit" data-id="${c.id}">Edit</button>
         <button class="btn btn-ghost btn-danger" data-action="delete" data-id="${c.id}">Delete</button>`
      : `<button class="btn" data-action="open" data-id="${c.id}">Connect</button>`;
    return `<tr>
      <td data-label="Name"><strong>${escapeHtml(c.name)}</strong>${mine ? "" : ' <span class="badge">shared</span>'}</td>
      <td data-label="Engine"><span class="badge">${escapeHtml(c.engine)}</span></td>
      <td data-label="Host" class="mono">${isFileBased(c.engine) ? '<span class="badge">server files</span>' : `${escapeHtml(c.host)}:${c.port}`}</td>
      <td data-label="Database" class="mono">${c.database ? escapeHtml(c.database) : '<span class="badge">all databases</span>'}</td>
      <td data-label="Actions" class="conn-actions">${ownerActions}</td></tr>`;
  }

  async _action(action, conn) {
    if (!conn) return;
    if (action === "test") {
      const done = await loading(
        "Testing connection…",
        isFileBased(conn.engine) ? "Opening the SQLite data directory" : `Reaching ${conn.host}:${conn.port}`
      );
      try {
        const r = await app.api.testConnection(conn.id);
        done();
        bus.emit(Events.TOAST, {
          message: r.ok ? `Connected — ${r.server_version || "OK"}` : `Failed: ${r.message}`,
          kind: r.ok ? "success" : "error",
        });
      } catch (err) {
        done();
        await alertError(err.message, "Connection test failed");
      }
    } else if (action === "open") {
      const done = await loading("Connecting…", `Establishing a session to ${conn.name}`);
      try {
        const session = await app.api.openSession(conn.id);
        done();
        setActiveSession(session);
        bus.emit(Events.TOAST, { message: `Connected to ${conn.name}`, kind: "success" });
        app.router.navigate("/editor");
      } catch (err) {
        done();
        await alertError(err.message, "Could not connect");
      }
    } else if (action === "edit") {
      this._openForm(conn);
    } else if (action === "delete") {
      const ok = await confirm({
        title: `Delete "${conn.name}"?`,
        text: "This removes the saved connection. Open sessions are unaffected.",
        confirmText: "Delete",
        danger: true,
      });
      if (!ok) return;
      try {
        await app.api.deleteConnection(conn.id);
        bus.emit(Events.TOAST, { message: "Connection deleted", kind: "success" });
        await this._load();
      } catch (err) {
        bus.emit(Events.TOAST, { message: err.message, kind: "error" });
      }
    }
  }

  _openForm(existing = null) {
    const form = document.createElement("form");
    form.className = "modal-form";
    form.innerHTML = `
      <div class="field"><label>Name</label>
        <input class="input" name="name" required value="${escapeHtml(existing?.name ?? "")}"></div>
      <div class="field"><label>Engine</label>
        <select class="input" name="engine" ${existing ? "disabled" : ""}>
          ${ENGINES.map(
            (e) =>
              `<option value="${e.value}" ${existing?.engine === e.value ? "selected" : ""}>${e.label}</option>`
          ).join("")}
        </select></div>
      <p class="sqlite-only muted" style="font-size:12.5px; margin:0 0 12px; line-height:1.5">
        SQLite databases are files stored on the server. Leave <strong>Database</strong> blank to
        browse and create database files from the editor's <strong>+ Database</strong> button, or
        enter an existing file name such as <span class="mono">sales.db</span>.
      </p>
      <div class="row network-only">
        <div class="field" style="flex:2"><label>Host</label>
          <input class="input" name="host" required value="${escapeHtml(existing?.host ?? "localhost")}"></div>
        <div class="field" style="flex:1"><label>Port</label>
          <input class="input" name="port" type="number" value="${existing?.port ?? 5432}"></div>
      </div>
      <div class="field"><label>Database <span class="muted">(optional — leave blank for server-level access to all databases)</span></label>
        <input class="input" name="database" placeholder="Leave blank to browse all databases"
          value="${escapeHtml(existing?.database ?? "")}"></div>
      <div class="field network-only"><label>Username</label>
        <input class="input" name="username" required value="${escapeHtml(existing?.username ?? "")}"></div>
      <div class="field network-only"><label>Password ${existing ? "(leave blank to keep)" : ""}</label>
        <input class="input" name="password" type="password" ${existing ? "" : "required"}></div>
      <div class="field network-only"><label>SSL mode (optional)</label>
        <input class="input" name="ssl_mode" placeholder="disable | require | verify-full"
          value="${escapeHtml(existing?.ssl_mode ?? "")}"></div>
      <div class="row" style="justify-content:flex-end; margin-top:8px">
        <button type="submit" class="btn btn-primary">${existing ? "Save" : "Create"}</button>
      </div>`;

    const engineSel = form.querySelector('[name="engine"]');
    // Show host/port/credentials only for network engines; SQLite needs just a name.
    const syncEngineFields = () => {
      const fileBased = isFileBased(engineSel.value);
      form.querySelectorAll(".network-only").forEach((el) => el.classList.toggle("hidden", fileBased));
      form.querySelectorAll(".sqlite-only").forEach((el) => el.classList.toggle("hidden", !fileBased));
      form.querySelector('[name="host"]').required = !fileBased;
      form.querySelector('[name="username"]').required = !fileBased;
      form.querySelector('[name="password"]').required = !fileBased && !existing;
      form.querySelector('[name="database"]').placeholder = fileBased
        ? "e.g. sales.db — or leave blank to browse/create files"
        : "Leave blank to browse all databases";
    };
    engineSel.addEventListener("change", () => {
      const e = ENGINES.find((x) => x.value === engineSel.value);
      if (e && !e.fileBased) form.querySelector('[name="port"]').value = e.port;
      syncEngineFields();
    });
    syncEngineFields();

    const close = openModal({
      title: existing ? "Edit connection" : "New connection",
      content: form,
      width: 460,
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(form).entries());
      const engine = existing?.engine ?? data.engine;
      if (isFileBased(engine)) {
        // SQLite: only a name and (optionally) a database file name.
        const payload = { name: data.name, database: data.database.trim() || null };
        try {
          if (existing) await app.api.updateConnection(existing.id, payload);
          else await app.api.createConnection({ ...payload, engine });
          bus.emit(Events.TOAST, { message: "Saved", kind: "success" });
          close();
          await this._load();
        } catch (err) {
          bus.emit(Events.TOAST, { message: err.message, kind: "error" });
        }
        return;
      }
      const payload = {
        name: data.name,
        host: data.host,
        port: Number(data.port) || undefined,
        database: data.database.trim() || null, // blank → server-level connection
        username: data.username,
        ssl_mode: data.ssl_mode || null,
      };
      if (data.password) payload.password = data.password;
      try {
        if (existing) {
          await app.api.updateConnection(existing.id, payload);
        } else {
          await app.api.createConnection({ ...payload, engine: data.engine });
        }
        bus.emit(Events.TOAST, { message: "Saved", kind: "success" });
        close();
        await this._load();
      } catch (err) {
        bus.emit(Events.TOAST, { message: err.message, kind: "error" });
      }
    });
  }
}

customElements.define("connections-view", ConnectionsView);
