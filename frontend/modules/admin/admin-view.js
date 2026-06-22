// Admin Panel: user management (create/edit role/activate/delete) and the audit log viewer.
// Tabs are role-gated by the backend (Admin for users, Admin/DBA for audit).

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { openModal } from "../../components/modal.js";
import { confirm } from "../../core/notify.js";
import { escapeHtml } from "../../components/view-helpers.js";

const ROLES = ["admin", "dba", "developer", "viewer"];

export class AdminView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="view" style="max-width:1100px">
        <div class="view-header"><h2>Admin</h2>
          <div class="muted">Manage users and review the immutable audit log.</div></div>
        <div class="tabs">
          <button class="tab active" data-tab="users">Users</button>
          <button class="tab" data-tab="permissions">Permissions</button>
          <button class="tab" data-tab="audit">Audit log</button>
        </div>
        <div class="panel" id="tabbody" style="padding:0; margin-top:12px"></div>
      </div>`;
    this._body = this.querySelector("#tabbody");
    this.querySelectorAll(".tab").forEach((t) =>
      t.addEventListener("click", () => this._select(t.dataset.tab))
    );
    this._select("users");
  }

  _select(tab) {
    this.querySelectorAll(".tab").forEach((t) =>
      t.classList.toggle("active", t.dataset.tab === tab)
    );
    if (tab === "users") this._renderUsers();
    else if (tab === "permissions") this._renderPermissions();
    else this._renderAudit();
  }

  // --- permissions (granular RBAC) -----------------------------------------------------

  async _renderPermissions() {
    this._body.innerHTML = `<div class="placeholder">Loading…</div>`;
    try {
      const [grants, connections] = await Promise.all([
        app.api.listGrants(),
        app.api.listConnections(),
      ]);
      this._connections = connections;
      const connName = (id) =>
        connections.find((c) => c.id === id)?.name || id.slice(0, 8);
      this._body.innerHTML = `
        <div class="row" style="padding:12px 16px; border-bottom:1px solid var(--border)">
          <span class="muted">${grants.length} grants — restrict which databases, tables and
            operations a user/role may use (admins are unrestricted)</span>
          <span class="spacer"></span>
          <button class="btn btn-primary" id="addgrant">+ New grant</button>
        </div>
        <table class="grid-table">
          <thead><tr><th>Subject</th><th>Connection</th><th>Database</th><th>Table</th>
            <th>Operations</th><th style="text-align:right">Actions</th></tr></thead>
          <tbody>${
            grants
              .map(
                (g) => `<tr>
              <td><span class="badge">${escapeHtml(g.subject_type)}</span> ${escapeHtml(g.subject_id.length > 12 ? g.subject_id.slice(0, 8) + "…" : g.subject_id)}</td>
              <td>${escapeHtml(connName(g.connection_id))}</td>
              <td class="mono">${escapeHtml(g.database || "*")}</td>
              <td class="mono">${escapeHtml(g.table_name || "*")}</td>
              <td>${g.operations.map((o) => `<span class="badge">${escapeHtml(o)}</span>`).join(" ")}</td>
              <td style="text-align:right; white-space:nowrap">
                <button class="btn btn-ghost" data-edit="${g.id}">Edit</button>
                <button class="btn btn-ghost btn-danger" data-del="${g.id}">Delete</button></td></tr>`
              )
              .join("") || `<tr><td colspan="6" class="muted" style="padding:16px">No grants — non-admin users fall back to role permissions.</td></tr>`
          }</tbody>
        </table>`;
      this._grants = grants;
      this.querySelector("#addgrant").addEventListener("click", () => this._grantForm());
      this._body.querySelectorAll("[data-edit]").forEach((btn) =>
        btn.addEventListener("click", () =>
          this._grantForm(this._grants.find((x) => x.id === btn.dataset.edit))
        )
      );
      this._body.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", () => this._deleteGrant(btn.dataset.del))
      );
    } catch (err) {
      this._body.innerHTML = `<div class="placeholder">${escapeHtml(err.message)}</div>`;
    }
  }

  async _deleteGrant(id) {
    if (!(await confirm({ title: "Delete this grant?", confirmText: "Delete", danger: true })))
      return;
    try {
      await app.api.deleteGrant(id);
      bus.emit(Events.TOAST, { message: "Grant deleted", kind: "success" });
      this._renderPermissions();
    } catch (err) {
      bus.emit(Events.TOAST, { message: err.message, kind: "error" });
    }
  }

  async _grantForm(existing = null) {
    let operations = [];
    let users = [];
    try {
      [operations, users] = await Promise.all([
        app.api.listGrantableOperations().then((r) => r.operations),
        app.api.listUsers(),
      ]);
    } catch {
      operations = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"];
    }
    const ROLES = ["admin", "dba", "developer", "viewer"];
    const subjOptions = (kind) =>
      kind === "user"
        ? users.map((u) => `<option value="${u.id}" ${existing?.subject_id === u.id ? "selected" : ""}>${escapeHtml(u.email)}</option>`).join("")
        : ROLES.map((r) => `<option value="${r}" ${existing?.subject_id === r ? "selected" : ""}>${r}</option>`).join("");

    const lockScope = Boolean(existing); // subject/connection are part of a grant's identity
    const form = document.createElement("form");
    form.className = "modal-form";
    form.innerHTML = `
      <div class="field"><label>Subject type</label>
        <select class="input" name="subject_type" ${lockScope ? "disabled" : ""}>
          <option value="role" ${existing?.subject_type === "role" ? "selected" : ""}>Role</option>
          <option value="user" ${existing?.subject_type === "user" ? "selected" : ""}>User</option>
        </select></div>
      <div class="field"><label>Subject</label>
        <select class="input" name="subject_id" ${lockScope ? "disabled" : ""}>
          ${subjOptions(existing?.subject_type || "role")}
        </select></div>
      <div class="field"><label>Connection</label>
        <select class="input" name="connection_id" ${lockScope ? "disabled" : ""}>
          ${(this._connections || []).map((c) => `<option value="${c.id}" ${existing?.connection_id === c.id ? "selected" : ""}>${escapeHtml(c.name)}</option>`).join("")}
        </select></div>
      <div class="row">
        <div class="field" style="flex:1"><label>Database (blank = any)</label>
          <input class="input" name="database" placeholder="*" value="${escapeHtml(existing?.database ?? "")}"></div>
        <div class="field" style="flex:1"><label>Table (blank = any)</label>
          <input class="input" name="table_name" placeholder="*" value="${escapeHtml(existing?.table_name ?? "")}"></div>
      </div>
      <div class="field"><label>Operations</label>
        <div class="ops-grid">
          ${operations
            .map((o) => {
              const checked = existing ? existing.operations.includes(o) : o === "SELECT";
              return `<label class="op-check"><input type="checkbox" value="${o}" ${checked ? "checked" : ""}> ${o}</label>`;
            })
            .join("")}
        </div></div>
      <div class="row" style="justify-content:flex-end"><button class="btn btn-primary">${existing ? "Save changes" : "Create grant"}</button></div>`;

    const typeSel = form.querySelector('[name="subject_type"]');
    const subjSel = form.querySelector('[name="subject_id"]');
    typeSel.addEventListener("change", () => {
      subjSel.innerHTML = subjOptions(typeSel.value);
    });

    const close = openModal({
      title: existing ? "Edit access grant" : "New access grant",
      content: form,
      width: 480,
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const ops = [...form.querySelectorAll(".op-check input:checked")].map((c) => c.value);
      if (!ops.length) {
        bus.emit(Events.TOAST, { message: "Select at least one operation", kind: "error" });
        return;
      }
      const d = Object.fromEntries(new FormData(form).entries());
      try {
        if (existing) {
          await app.api.updateGrant(existing.id, {
            operations: ops,
            database: d.database || null,
            table_name: d.table_name || null,
          });
          bus.emit(Events.TOAST, { message: "Grant updated", kind: "success" });
        } else {
          await app.api.createGrant({
            subject_type: d.subject_type,
            subject_id: d.subject_id,
            connection_id: d.connection_id,
            operations: ops,
            database: d.database || null,
            table_name: d.table_name || null,
          });
          bus.emit(Events.TOAST, { message: "Grant created", kind: "success" });
        }
        close();
        this._renderPermissions();
      } catch (err) {
        bus.emit(Events.TOAST, { message: err.message, kind: "error" });
      }
    });
  }

  // --- users ---------------------------------------------------------------------------

  async _renderUsers() {
    this._body.innerHTML = `<div class="placeholder">Loading…</div>`;
    try {
      const users = await app.api.listUsers();
      this._body.innerHTML = `
        <div class="row" style="padding:12px 16px; border-bottom:1px solid var(--border)">
          <span class="muted">${users.length} users</span><span class="spacer"></span>
          <button class="btn btn-primary" id="adduser">+ New user</button>
        </div>
        <table class="grid-table">
          <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Last login</th>
            <th style="text-align:right">Actions</th></tr></thead>
          <tbody>${users.map((u) => this._userRow(u)).join("")}</tbody>
        </table>`;
      this.querySelector("#adduser").addEventListener("click", () => this._userForm());
      this._body.querySelectorAll("[data-action]").forEach((btn) =>
        btn.addEventListener("click", () =>
          this._userAction(btn.dataset.action, users.find((u) => u.id === btn.dataset.id))
        )
      );
    } catch (err) {
      this._body.innerHTML = `<div class="placeholder">${escapeHtml(err.message)}</div>`;
    }
  }

  _userRow(u) {
    return `<tr>
      <td><strong>${escapeHtml(u.email)}</strong></td>
      <td><span class="badge">${escapeHtml(u.role)}</span></td>
      <td>${u.is_active ? '<span style="color:var(--success)">active</span>' : '<span class="muted">disabled</span>'}</td>
      <td class="muted">${u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "—"}</td>
      <td style="text-align:right; white-space:nowrap">
        <button class="btn btn-ghost" data-action="edit" data-id="${u.id}">Edit</button>
        <button class="btn btn-ghost btn-danger" data-action="delete" data-id="${u.id}">Delete</button>
      </td></tr>`;
  }

  async _userAction(action, user) {
    if (!user) return;
    if (action === "edit") return this._userForm(user);
    if (action === "delete") {
      if (
        !(await confirm({
          title: `Delete user ${user.email}?`,
          confirmText: "Delete",
          danger: true,
        }))
      )
        return;
      try {
        await app.api.deleteUser(user.id);
        bus.emit(Events.TOAST, { message: "User deleted", kind: "success" });
        this._renderUsers();
      } catch (err) {
        bus.emit(Events.TOAST, { message: err.message, kind: "error" });
      }
    }
  }

  _userForm(existing = null) {
    const form = document.createElement("form");
    form.className = "modal-form";
    form.innerHTML = `
      <div class="field"><label>Email</label>
        <input class="input" name="email" type="email" required
          ${existing ? "disabled" : ""} value="${escapeHtml(existing?.email ?? "")}"></div>
      ${existing ? "" : `<div class="field"><label>Password</label>
        <input class="input" name="password" type="password" required minlength="12"></div>`}
      <div class="field"><label>Role</label>
        <select class="input" name="role">
          ${ROLES.map((r) => `<option value="${r}" ${existing?.role === r ? "selected" : ""}>${r}</option>`).join("")}
        </select></div>
      ${existing ? `<div class="field"><label>Status</label>
        <select class="input" name="is_active">
          <option value="true" ${existing.is_active ? "selected" : ""}>Active</option>
          <option value="false" ${!existing.is_active ? "selected" : ""}>Disabled</option>
        </select></div>` : ""}
      <div class="row" style="justify-content:flex-end"><button class="btn btn-primary">
        ${existing ? "Save" : "Create"}</button></div>`;

    const close = openModal({ title: existing ? "Edit user" : "New user", content: form });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(form).entries());
      try {
        if (existing) {
          await app.api.updateUser(existing.id, {
            role: d.role,
            is_active: d.is_active === "true",
          });
        } else {
          await app.api.createUser({ email: d.email, password: d.password, role: d.role });
        }
        bus.emit(Events.TOAST, { message: "Saved", kind: "success" });
        close();
        this._renderUsers();
      } catch (err) {
        bus.emit(Events.TOAST, { message: err.message, kind: "error" });
      }
    });
  }

  // --- audit ---------------------------------------------------------------------------

  async _renderAudit() {
    this._body.innerHTML = `
      <div class="row" style="padding:12px 16px; border-bottom:1px solid var(--border); gap:8px">
        <select class="input" id="f-cat" style="width:auto">
          <option value="">All categories</option>
          <option value="read">read</option><option value="write">write</option>
          <option value="ddl">ddl</option>
        </select>
        <label class="row" style="gap:6px"><input type="checkbox" id="f-dest"> destructive only</label>
        <label class="row" style="gap:6px"><input type="checkbox" id="f-fail"> failures only</label>
        <span class="spacer"></span>
        <button class="btn" id="reload">Refresh</button>
      </div>
      <div id="auditrows"><div class="placeholder">Loading…</div></div>`;
    this.querySelector("#reload").addEventListener("click", () => this._loadAudit());
    ["f-cat", "f-dest", "f-fail"].forEach((id) =>
      this.querySelector("#" + id).addEventListener("change", () => this._loadAudit())
    );
    this._loadAudit();
  }

  async _loadAudit() {
    const filters = {};
    const cat = this.querySelector("#f-cat").value;
    if (cat) filters.category = cat;
    if (this.querySelector("#f-dest").checked) filters.destructive = true;
    if (this.querySelector("#f-fail").checked) filters.success = false;
    const box = this.querySelector("#auditrows");
    try {
      const logs = await app.api.listAuditLogs({ ...filters, limit: 200 });
      if (!logs.length) {
        box.innerHTML = `<div class="placeholder muted">No audit entries.</div>`;
        return;
      }
      box.innerHTML = `
        <table class="grid-table">
          <thead><tr><th>Time</th><th>User</th><th>Engine</th><th>Cat</th>
            <th>Statement</th><th>Result</th><th>ms</th></tr></thead>
          <tbody>${logs.map((l) => this._auditRow(l)).join("")}</tbody>
        </table>`;
    } catch (err) {
      box.innerHTML = `<div class="placeholder">${escapeHtml(err.message)}</div>`;
    }
  }

  _auditRow(l) {
    const cat = { read: "cat-read", write: "cat-write", ddl: "cat-ddl" }[l.category] || "";
    return `<tr>
      <td class="muted" style="white-space:nowrap">${new Date(l.created_at).toLocaleString()}</td>
      <td>${escapeHtml(l.user_email || "—")}</td>
      <td class="muted">${escapeHtml(l.engine || "")}</td>
      <td><span class="badge ${cat}">${escapeHtml(l.category || "")}</span>
        ${l.destructive ? '<span class="badge cat-ddl">⚠</span>' : ""}</td>
      <td class="mono" style="max-width:340px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap"
        title="${escapeHtml(l.statement)}">${escapeHtml(l.statement)}</td>
      <td>${l.success ? '<span style="color:var(--success)">ok</span>' : `<span style="color:var(--danger)">${escapeHtml(l.error_code || "fail")}</span>`}</td>
      <td class="mono">${Math.round(l.duration_ms)}</td></tr>`;
  }
}

customElements.define("admin-view", AdminView);
