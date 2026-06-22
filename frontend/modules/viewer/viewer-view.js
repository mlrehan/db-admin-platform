// Data Viewer: pick a schema + table and browse rows with server-side pagination and
// optional server-side ordering, rendered in the data grid.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore } from "../../core/session-state.js";
import { buildSelect } from "../../core/sql.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";
import "../../components/data-grid.js";

const PAGE_SIZE = 100;

export class ViewerView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="viewer-layout">
        <div class="editor-toolbar"><session-bar></session-bar></div>
        <div class="viewer-controls">
          <select class="input" id="schema"><option value="">Schema…</option></select>
          <select class="input" id="table" disabled><option value="">Table…</option></select>
          <select class="input" id="order"><option value="">No ordering</option></select>
          <select class="input" id="dir"><option value="asc">ASC</option><option value="desc">DESC</option></select>
          <span class="spacer"></span>
          <button class="btn btn-ghost" id="prev" disabled>‹ Prev</button>
          <span class="muted" id="pageinfo">—</span>
          <button class="btn btn-ghost" id="next" disabled>Next ›</button>
        </div>
        <data-grid id="grid"></data-grid>
      </div>`;

    this._grid = this.querySelector("#grid");
    this._schemaSel = this.querySelector("#schema");
    this._tableSel = this.querySelector("#table");
    this._orderSel = this.querySelector("#order");
    this._dirSel = this.querySelector("#dir");
    this._offset = 0;

    this._schemaSel.addEventListener("change", () => this._loadTables());
    this._tableSel.addEventListener("change", () => {
      this._offset = 0;
      this._loadColumnsThenPage();
    });
    this._orderSel.addEventListener("change", () => {
      this._offset = 0;
      this._loadPage();
    });
    this._dirSel.addEventListener("change", () => {
      this._offset = 0;
      this._loadPage();
    });
    this.querySelector("#prev").addEventListener("click", () => {
      this._offset = Math.max(0, this._offset - PAGE_SIZE);
      this._loadPage();
    });
    this.querySelector("#next").addEventListener("click", () => {
      this._offset += PAGE_SIZE;
      this._loadPage();
    });

    this._unsub = sessionStore.subscribe(() => this._loadSchemas());
    if (sessionStore.getState().sessionId) this._loadSchemas();
  }

  disconnectedCallback() {
    this._unsub?.();
  }

  _ctx() {
    const { sessionId, engine } = sessionStore.getState();
    return { sessionId, engine };
  }

  async _loadSchemas() {
    const { sessionId } = this._ctx();
    if (!sessionId) return;
    try {
      const schemas = await app.api.listSchemas(sessionId);
      this._schemaSel.innerHTML =
        `<option value="">Schema…</option>` +
        schemas
          .map((s) => `<option value="${escapeHtml(s.name)}" ${s.is_default ? "selected" : ""}>${escapeHtml(s.name)}</option>`)
          .join("");
      if (schemas.some((s) => s.is_default)) this._loadTables();
    } catch (err) {
      bus.emit(Events.TOAST, { message: err.message, kind: "error" });
    }
  }

  async _loadTables() {
    const { sessionId } = this._ctx();
    const schema = this._schemaSel.value;
    this._tableSel.innerHTML = `<option value="">Table…</option>`;
    this._tableSel.disabled = true;
    if (!schema) return;
    const tables = await app.api.listTables(sessionId, schema);
    this._tableSel.innerHTML =
      `<option value="">Table…</option>` +
      tables.map((t) => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`).join("");
    this._tableSel.disabled = false;
  }

  async _loadColumnsThenPage() {
    const { sessionId } = this._ctx();
    const schema = this._schemaSel.value;
    const table = this._tableSel.value;
    this._orderSel.innerHTML = `<option value="">No ordering</option>`;
    if (table) {
      try {
        const detail = await app.api.describeTable(sessionId, table, schema);
        this._orderSel.innerHTML =
          `<option value="">No ordering</option>` +
          detail.columns.map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join("");
      } catch {
        /* ordering optional */
      }
    }
    this._loadPage();
  }

  async _loadPage() {
    const { sessionId, engine } = this._ctx();
    const schema = this._schemaSel.value;
    const table = this._tableSel.value;
    if (!table) return;
    const sql = buildSelect({
      engine,
      schema,
      table,
      limit: PAGE_SIZE,
      offset: this._offset,
      orderBy: this._orderSel.value || null,
      direction: this._dirSel.value,
    });
    this._grid.setBusy(true, "Loading data…");
    try {
      const res = await app.api.executeQuery(sessionId, sql);
      this._grid.setData(res.columns, res.rows);
      const page = Math.floor(this._offset / PAGE_SIZE) + 1;
      this.querySelector("#pageinfo").textContent = `Page ${page} · ${res.row_count} rows`;
      this.querySelector("#prev").disabled = this._offset === 0;
      this.querySelector("#next").disabled = res.row_count < PAGE_SIZE;
    } catch (err) {
      this._grid.setBusy(false);
      bus.emit(Events.TOAST, { message: err.message, kind: "error" });
    }
  }
}

customElements.define("viewer-view", ViewerView);
