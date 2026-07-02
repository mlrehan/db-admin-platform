// Reusable audit-log table: filters (category / outcome / date range), infinite scroll,
// resizable columns, selectable text, and CSV / Excel export. Access scoping is enforced by
// the backend — a regular user only ever receives their own records, an admin/auditor receives
// all — so the same component serves both the admin "Audit log" tab and the user "Activity"
// view without any client-side trust.

import { app } from "../core/context.js";
import { bus, Events } from "../core/events.js";
import { confirm, toast } from "../core/notify.js";
import { escapeHtml } from "./view-helpers.js";
import { makeResizableTable } from "./resizable-table.js";

const PAGE_SIZE = 100;
const EXPORT_PAGE = 500; // backend max per request
const EXPORT_CAP = 20000; // safety ceiling for a single export

// `admin: true` columns (the IP addresses) are shown ONLY to admins; non-admins never receive
// or see any IP column. `public`/`private` distinguish the edge-observed and LAN addresses.
const COLUMNS = [
  { key: "created_at", label: "Time", get: (l) => new Date(l.created_at).toLocaleString() },
  { key: "user_email", label: "User", get: (l) => l.user_email || "—" },
  { key: "connection_name", label: "Server", get: (l) => l.connection_name || "—" },
  { key: "ip_address", label: "Public IP", admin: true, get: (l) => l.ip_address || "—" },
  { key: "ip_private", label: "Private IP", admin: true, get: (l) => l.ip_private || "—" },
  { key: "engine", label: "Engine", get: (l) => l.engine || "" },
  { key: "category", label: "Category", get: (l) => l.category || "" },
  { key: "statement", label: "Statement", get: (l) => l.statement || "" },
  { key: "result", label: "Result", get: (l) => (l.success ? "ok" : l.error_code || "fail") },
  { key: "row_count", label: "Rows", get: (l) => (l.row_count ?? l.rows_affected ?? "") },
  { key: "duration_ms", label: "ms", get: (l) => Math.round(l.duration_ms) },
];

export class AuditLog extends HTMLElement {
  connectedCallback() {
    this._isAdmin = app.auth?.user?.role === "admin";
    // Non-admins never see any IP column; only admins get the public/private IP columns.
    this._columns = COLUMNS.filter((c) => !c.admin || this._isAdmin);

    this.innerHTML = `
      <div class="row" style="padding:12px 16px; border-bottom:1px solid var(--border); gap:8px; flex-wrap:wrap">
        <select class="input" id="f-user" style="width:auto" hidden>
          <option value="">All users</option>
        </select>
        <select class="input" id="f-cat" style="width:auto">
          <option value="">All categories</option>
          <option value="read">read</option><option value="write">write</option>
          <option value="ddl">ddl</option>
        </select>
        <label class="row" style="gap:6px"><input type="checkbox" id="f-dest"> destructive</label>
        <label class="row" style="gap:6px"><input type="checkbox" id="f-fail"> failures</label>
        <label class="row" style="gap:6px">From <input type="date" class="input" id="f-since" style="width:auto"></label>
        <label class="row" style="gap:6px">To <input type="date" class="input" id="f-until" style="width:auto"></label>
        <span class="spacer"></span>
        <button class="btn btn-danger" id="delete-sel" ${this._isAdmin ? "" : "hidden"} disabled>🗑 Delete</button>
        <button class="btn btn-ghost" id="export-csv">⬇ CSV</button>
        <button class="btn btn-ghost" id="export-xls">⬇ Excel</button>
        <button class="btn" id="reload">Refresh</button>
      </div>
      <div id="auditrows" class="table-scroll"><div class="placeholder">Loading…</div></div>`;

    this._rowsBox = this.querySelector("#auditrows");
    this._connNames = new Map();
    this.querySelector("#reload").addEventListener("click", () => this._load());
    ["f-user", "f-cat", "f-dest", "f-fail", "f-since", "f-until"].forEach((id) =>
      this.querySelector("#" + id).addEventListener("change", () => this._load())
    );
    this.querySelector("#export-csv").addEventListener("click", () => this._export("csv"));
    this.querySelector("#export-xls").addEventListener("click", () => this._export("xls"));
    if (this._isAdmin) {
      this.querySelector("#delete-sel").addEventListener("click", () => this._deleteSelected());
      // Selection lives inside the scroll box (checkboxes are rendered per row).
      this._rowsBox.addEventListener("change", (e) => this._onSelectionChange(e));
    }
    this._init();
  }

  _selectedIds() {
    return [...this.querySelectorAll(".sel-row:checked")].map((cb) => cb.dataset.id);
  }

  _onSelectionChange(e) {
    if (e.target.id === "sel-all") {
      const on = e.target.checked;
      this.querySelectorAll(".sel-row").forEach((cb) => (cb.checked = on));
    }
    const count = this._selectedIds().length;
    const btn = this.querySelector("#delete-sel");
    if (btn) {
      btn.disabled = count === 0;
      btn.textContent = count ? `🗑 Delete (${count})` : "🗑 Delete";
    }
  }

  async _deleteSelected() {
    const ids = this._selectedIds();
    if (!ids.length) return;
    const ok = await confirm({
      title: `Delete ${ids.length} audit record(s)?`,
      text: "This permanently removes the selected audit entries. This cannot be undone.",
      confirmText: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await app.api.deleteAuditLogs(ids);
      toast(`Deleted ${res?.deleted ?? ids.length} record(s)`, "success");
      this._load();
    } catch (err) {
      toast(err?.message || "Delete failed", "error");
    }
  }

  // Resolve connection (server) names, and — for admins — populate the "filter by user" select.
  async _init() {
    const isAdmin = app.auth?.user?.role === "admin";
    try {
      const conns = await app.api.listConnections({ allOwners: isAdmin });
      (conns || []).forEach((c) => this._connNames.set(c.id, c.name));
    } catch {
      /* server-name column is best-effort */
    }
    if (isAdmin) {
      try {
        const users = await app.api.listUsers();
        const sel = this.querySelector("#f-user");
        sel.innerHTML =
          '<option value="">All users</option>' +
          users.map((u) => `<option value="${u.id}">${escapeHtml(u.email)}</option>`).join("");
        sel.hidden = false;
      } catch {
        /* user filter is admin-only and optional */
      }
    }
    this._load();
  }

  // Attach the resolved connection name to each row (for the Server column + exports).
  _enrich(logs) {
    logs.forEach((l) => {
      l.connection_name = this._connNames.get(l.connection_id) || "";
    });
    return logs;
  }

  disconnectedCallback() {
    this._observer?.disconnect();
  }

  _filters() {
    const f = {};
    const user = this.querySelector("#f-user")?.value;
    if (user) f.user_id = user; // admin-only; ignored/forced server-side for non-admins
    const cat = this.querySelector("#f-cat").value;
    if (cat) f.category = cat;
    if (this.querySelector("#f-dest").checked) f.destructive = true;
    if (this.querySelector("#f-fail").checked) f.success = false;
    const since = this.querySelector("#f-since").value;
    const until = this.querySelector("#f-until").value;
    if (since) f.since = new Date(since + "T00:00:00").toISOString();
    if (until) f.until = new Date(until + "T23:59:59").toISOString();
    return f;
  }

  // Render one <td> for a given column key (special-cased styling for a few cells).
  _cell(l, key) {
    if (key === "created_at")
      return `<td class="muted" style="white-space:nowrap">${escapeHtml(new Date(l.created_at).toLocaleString())}</td>`;
    if (key === "ip_address" || key === "ip_private")
      return `<td class="mono">${escapeHtml(l[key] || "—")}</td>`;
    if (key === "engine") return `<td class="muted">${escapeHtml(l.engine || "")}</td>`;
    if (key === "category") {
      const cat = { read: "cat-read", write: "cat-write", ddl: "cat-ddl" }[l.category] || "";
      return `<td><span class="badge ${cat}">${escapeHtml(l.category || "")}</span>${l.destructive ? ' <span class="badge cat-ddl">⚠</span>' : ""}</td>`;
    }
    if (key === "statement")
      return `<td class="mono stmt-cell" style="min-width:280px; max-width:560px; white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; overflow:visible; text-overflow:clip; user-select:text">${escapeHtml(l.statement)}</td>`;
    if (key === "result")
      return l.success
        ? '<td><span style="color:var(--success)">ok</span></td>'
        : `<td><span style="color:var(--danger)">${escapeHtml(l.error_code || "fail")}</span></td>`;
    if (key === "row_count") return `<td class="mono">${l.row_count ?? l.rows_affected ?? ""}</td>`;
    if (key === "duration_ms") return `<td class="mono">${Math.round(l.duration_ms)}</td>`;
    const col = this._columns.find((c) => c.key === key);
    return `<td>${escapeHtml(String(col ? col.get(l) : l[key] ?? ""))}</td>`;
  }

  _row(l) {
    const sel = this._isAdmin
      ? `<td><input type="checkbox" class="sel-row" data-id="${escapeHtml(l.id)}"></td>`
      : "";
    return `<tr>${sel}${this._columns.map((c) => this._cell(l, c.key)).join("")}</tr>`;
  }

  _headerCells() {
    const sel = this._isAdmin ? '<th><input type="checkbox" id="sel-all" title="Select all"></th>' : "";
    return sel + this._columns.map((c) => `<th><span class="th-label">${c.label}</span></th>`).join("");
  }

  async _load() {
    this._observer?.disconnect();
    this._offset = 0;
    this._done = false;
    this._loading = false;
    this._rowsBox.innerHTML = `<div class="placeholder">Loading…</div>`;
    let logs;
    try {
      logs = this._enrich(await app.api.listAuditLogs({ ...this._filters(), limit: PAGE_SIZE, offset: 0 }));
    } catch (err) {
      this._rowsBox.innerHTML = `<div class="placeholder">${escapeHtml(err.message)}</div>`;
      return;
    }
    if (!logs.length) {
      this._rowsBox.innerHTML = `<div class="placeholder muted">No audit entries.</div>`;
      return;
    }
    // #auditrows is the bounded scroll container (sticky headers, vertical scroll for infinite
    // load, horizontal scroll at its bottom). The sentinel lives inside it and the observer is
    // rooted to it so "load more" fires on scrolling the table, not the page.
    this._rowsBox.innerHTML = `
      <table class="grid-table wrap" id="audit-table">
        <thead><tr>${this._headerCells()}</tr></thead>
        <tbody id="auditbody">${logs.map((l) => this._row(l)).join("")}</tbody>
      </table>
      <div id="audit-sentinel" class="muted" style="padding:12px; text-align:center"></div>`;
    makeResizableTable(this.querySelector("#audit-table"));
    this._offset = logs.length;
    if (logs.length < PAGE_SIZE) {
      this._done = true;
      this.querySelector("#audit-sentinel").textContent = "— end of log —";
      return;
    }
    const sentinel = this.querySelector("#audit-sentinel");
    this._observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) this._loadMore();
      },
      { root: this._rowsBox }
    );
    this._observer.observe(sentinel);
  }

  async _loadMore() {
    if (this._loading || this._done) return;
    this._loading = true;
    const sentinel = this.querySelector("#audit-sentinel");
    const body = this.querySelector("#auditbody");
    if (sentinel) sentinel.textContent = "Loading more…";
    try {
      const logs = this._enrich(await app.api.listAuditLogs({
        ...this._filters(),
        limit: PAGE_SIZE,
        offset: this._offset,
      }));
      if (body && logs.length) body.insertAdjacentHTML("beforeend", logs.map((l) => this._row(l)).join(""));
      this._offset += logs.length;
      if (logs.length < PAGE_SIZE) {
        this._done = true;
        this._observer?.disconnect();
        if (sentinel) sentinel.textContent = "— end of log —";
      } else if (sentinel) {
        sentinel.textContent = "";
      }
    } catch (err) {
      if (sentinel) sentinel.textContent = err.message;
    } finally {
      this._loading = false;
    }
  }

  // Fetch every row matching the CURRENT filters (backend-scoped to what the user may see).
  async _fetchAll() {
    const out = [];
    let offset = 0;
    for (;;) {
      const page = await app.api.listAuditLogs({
        ...this._filters(),
        limit: EXPORT_PAGE,
        offset,
      });
      out.push(...this._enrich(page));
      offset += page.length;
      if (page.length < EXPORT_PAGE || out.length >= EXPORT_CAP) break;
    }
    return out;
  }

  async _export(format) {
    const btn = this.querySelector(format === "csv" ? "#export-csv" : "#export-xls");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Exporting…";
    try {
      const rows = await this._fetchAll();
      if (!rows.length) {
        bus.emit(Events.TOAST, { message: "Nothing to export.", kind: "info" });
        return;
      }
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      if (format === "csv") {
        downloadBlob(toCsv(rows, this._columns), `audit-log-${stamp}.csv`, "text/csv;charset=utf-8");
      } else {
        downloadBlob(toExcelHtml(rows, this._columns), `audit-log-${stamp}.xls`, "application/vnd.ms-excel");
      }
      bus.emit(Events.TOAST, { message: `Exported ${rows.length} row(s)`, kind: "success" });
    } catch (err) {
      bus.emit(Events.TOAST, { message: err?.message || "Export failed", kind: "error" });
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }
}

function toCsv(rows, columns = COLUMNS) {
  const esc = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = columns.map((c) => esc(c.label)).join(",");
  const body = rows.map((r) => columns.map((c) => esc(c.get(r))).join(",")).join("\n");
  return "﻿" + header + "\n" + body; // BOM so Excel reads UTF-8 correctly
}

function toExcelHtml(rows, columns = COLUMNS) {
  const cell = (v) => `<td>${escapeHtml(String(v ?? ""))}</td>`;
  const head = columns.map((c) => `<th>${escapeHtml(c.label)}</th>`).join("");
  const body = rows.map((r) => `<tr>${columns.map((c) => cell(c.get(r))).join("")}</tr>`).join("");
  return `<html><head><meta charset="utf-8"></head><body><table border="1"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></body></html>`;
}

function downloadBlob(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

customElements.define("audit-log", AuditLog);
