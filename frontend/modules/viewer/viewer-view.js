// Data Viewer: pick a schema + table and browse rows with server-side pagination and
// optional server-side ordering, rendered in the data grid.
//
// Paging is done on the server (LIMIT/OFFSET or OFFSET/FETCH), so this view owns the ONE pager:
// "Rows 101–200 of 1,234 · Page 2 of 13". The grid is told it is server-paged so it does not
// add a second, client-side pager over the current page. Pages are ordered by the primary key
// (as a tiebreaker after any chosen ordering) so rows never repeat or go missing between pages.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore } from "../../core/session-state.js";
import { buildCount, buildSelect } from "../../core/sql.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";
import "../../components/data-grid.js";

const PAGE_SIZES = [50, 100, 250, 500, 1000];
const DEFAULT_PAGE_SIZE = 100;
const PAGE_SIZE_KEY = "dbadmin.viewer-page-size";

function savedPageSize() {
  try {
    const n = Number(localStorage.getItem(PAGE_SIZE_KEY));
    return PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE;
  } catch {
    return DEFAULT_PAGE_SIZE;
  }
}

const fmt = (n) => Number(n).toLocaleString();

export class ViewerView extends HTMLElement {
  connectedCallback() {
    this._pageSize = savedPageSize();
    this.innerHTML = `
      <div class="viewer-layout">
        <div class="editor-toolbar"><session-bar></session-bar></div>
        <div class="viewer-controls">
          <select class="input" id="schema"><option value="">Schema…</option></select>
          <select class="input" id="table" disabled><option value="">Table…</option></select>
          <select class="input" id="order"><option value="">No ordering</option></select>
          <select class="input" id="dir"><option value="asc">ASC</option><option value="desc">DESC</option></select>
          <span class="spacer"></span>
          <div class="viewer-pager" role="navigation" aria-label="Table pages">
            <label class="muted viewer-pagesize">Rows per page
              <select class="input" id="pagesize">
                ${PAGE_SIZES.map((n) => `<option value="${n}" ${n === this._pageSize ? "selected" : ""}>${n}</option>`).join("")}
              </select>
            </label>
            <button class="btn btn-ghost" id="first" title="First page" aria-label="First page" disabled>«</button>
            <button class="btn btn-ghost" id="prev" title="Previous page" aria-label="Previous page" disabled>‹</button>
            <span class="muted viewer-pageinfo" id="pageinfo" aria-live="polite">—</span>
            <button class="btn btn-ghost" id="next" title="Next page" aria-label="Next page" disabled>›</button>
            <button class="btn btn-ghost" id="last" title="Last page" aria-label="Last page" disabled>»</button>
          </div>
        </div>
        <data-grid id="grid" server-paged></data-grid>
      </div>`;

    this._grid = this.querySelector("#grid");
    this._schemaSel = this.querySelector("#schema");
    this._tableSel = this.querySelector("#table");
    this._orderSel = this.querySelector("#order");
    this._dirSel = this.querySelector("#dir");
    this._offset = 0;
    this._total = null; // total row count of the table (null = unknown / still counting)
    this._keyColumns = []; // primary key → deterministic page order
    this._pageRows = 0;
    this._countSeq = 0; // ignore stale COUNT(*) results after the table changes

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
    this.querySelector("#pagesize").addEventListener("change", (e) => {
      this._pageSize = Number(e.target.value) || DEFAULT_PAGE_SIZE;
      try {
        localStorage.setItem(PAGE_SIZE_KEY, String(this._pageSize));
      } catch {
        /* ignore */
      }
      // Keep the first visible row on screen: jump to the page that contains it.
      this._offset = Math.floor(this._offset / this._pageSize) * this._pageSize;
      this._loadPage();
    });
    this.querySelector("#first").addEventListener("click", () => this._goTo(0));
    this.querySelector("#prev").addEventListener("click", () => this._goTo(this._offset - this._pageSize));
    this.querySelector("#next").addEventListener("click", () => this._goTo(this._offset + this._pageSize));
    this.querySelector("#last").addEventListener("click", () => {
      if (this._total) this._goTo(Math.floor((this._total - 1) / this._pageSize) * this._pageSize);
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

  _goTo(offset) {
    this._offset = Math.max(0, offset);
    this._loadPage();
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
    this._tableSel.innerHTML = `<option value="">Loading tables…</option>`;
    try {
      const tables = await app.api.listTables(sessionId, schema);
      this._tableSel.innerHTML =
        `<option value="">Table…</option>` +
        tables.map((t) => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`).join("");
      this._tableSel.disabled = false;
    } catch (err) {
      this._tableSel.innerHTML = `<option value="">Table…</option>`;
      bus.emit(Events.TOAST, { message: err.message, kind: "error" });
    }
  }

  async _loadColumnsThenPage() {
    const { sessionId } = this._ctx();
    const schema = this._schemaSel.value;
    const table = this._tableSel.value;
    this._orderSel.innerHTML = `<option value="">No ordering</option>`;
    this._keyColumns = [];
    this._total = null;
    this._renderPager();
    // Show the loading indicator immediately on table selection — it must cover the (possibly
    // slow) describe-table introspection AND the first page fetch, not just the page fetch.
    if (table) {
      this._grid.setBusy(true, "Loading table…");
      try {
        const detail = await app.api.describeTable(sessionId, table, schema);
        this._orderSel.innerHTML =
          `<option value="">No ordering</option>` +
          detail.columns.map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join("");
        this._keyColumns = detail.primary_key || [];
      } catch {
        /* ordering optional */
      }
      this._loadCount(); // runs alongside the first page; the pager fills in "of N" when ready
    }
    this._loadPage();
  }

  // Total rows for "of N" and the Last button. Counted once per table (not per page). If it
  // fails (e.g. a huge table times out), the pager still works without the total.
  async _loadCount() {
    const { sessionId, engine } = this._ctx();
    const schema = this._schemaSel.value;
    const table = this._tableSel.value;
    if (!table) return;
    const seq = ++this._countSeq;
    try {
      const res = await app.api.executeQuery(sessionId, buildCount({ engine, schema, table }));
      if (seq !== this._countSeq) return;
      const n = Number(res.rows?.[0]?.[0]);
      this._total = Number.isFinite(n) ? n : null;
    } catch {
      if (seq === this._countSeq) this._total = null;
    }
    if (seq === this._countSeq) this._renderPager();
  }

  _renderPager() {
    const info = this.querySelector("#pageinfo");
    const size = this._pageSize;
    const page = Math.floor(this._offset / size) + 1;
    const from = this._pageRows ? this._offset + 1 : 0;
    const to = this._offset + this._pageRows;
    const known = this._total !== null;
    const pages = known ? Math.max(1, Math.ceil(this._total / size)) : null;

    if (!this._tableSel.value) {
      info.textContent = "—";
    } else if (known) {
      info.textContent = this._total === 0
        ? "No rows"
        : `Rows ${fmt(from)}–${fmt(to)} of ${fmt(this._total)} · Page ${fmt(page)} of ${fmt(pages)}`;
    } else {
      info.textContent = this._pageRows
        ? `Rows ${fmt(from)}–${fmt(to)} · Page ${fmt(page)}`
        : `Page ${fmt(page)} · no rows`;
    }
    const atStart = this._offset === 0;
    // Without a total, a full page means there may be more rows.
    const atEnd = known ? this._offset + size >= this._total : this._pageRows < size;
    this.querySelector("#first").disabled = atStart;
    this.querySelector("#prev").disabled = atStart;
    this.querySelector("#next").disabled = atEnd;
    this.querySelector("#last").disabled = !known || atEnd;
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
      limit: this._pageSize,
      offset: this._offset,
      orderBy: this._orderSel.value || null,
      direction: this._dirSel.value,
      keyColumns: this._keyColumns,
    });
    this._grid.setBusy(true, "Loading data…");
    try {
      const res = await app.api.executeQuery(sessionId, sql);
      // Rows were deleted since counting and we paged past the end: show the last real page.
      if (res.row_count === 0 && this._offset > 0) {
        this._offset = Math.max(0, this._offset - this._pageSize);
        this._loadCount();
        return this._loadPage();
      }
      this._pageRows = res.row_count;
      this._grid.setData(res.columns, res.rows);
      this._renderPager();
    } catch (err) {
      this._grid.setBusy(false);
      bus.emit(Events.TOAST, { message: err.message, kind: "error" });
    }
  }
}

customElements.define("viewer-view", ViewerView);
