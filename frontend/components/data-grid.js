// <data-grid> — the results grid used by the SQL Editor and the Data Viewer.
//
//   grid.setData(columns, rows, { truncated, rowOffset })
//     columns: [{ name, kind }]  (kind from the server: integer | decimal | date | text | …)
//     rows:    array of arrays, values exactly as the database returned them (null = SQL NULL)
//
// Renders with AG Grid (virtualized rows and columns — thousands of rows stay smooth; sticky
// header; resizable/sortable/filterable columns; keyboard navigation) and falls back to a
// built-in table if AG Grid can't load. Both renderers share the same cell formatting
// (core/cell-format.js): NULL, '' and real values are always visually distinct, numbers are
// right-aligned and sorted numerically, dates/binary/JSON are formatted, long text is
// ellipsized with a tooltip. Both share one footer: row count, wrap toggle, CSV export and the
// pager (rows per page, range, first/prev/next/last). `<data-grid server-paged>` (Data Viewer)
// hides the pager — its owner pages on the server — and numbers rows from `rowOffset`.

import { sortRows, paginate, pageCount } from "./grid-utils.js";
import { config } from "../core/config.js";
import { isDarkTheme, THEME_EVENT } from "../core/theme.js";
import {
  cellHtml,
  compareCells,
  toCsv,
  escapeCell,
  estimateColumnWidth,
  formatCell,
  headerMinWidth,
  isNumericKind,
  kindLabel,
} from "../core/cell-format.js";

const AG_BASE = config.agGridBase;
const FIRST_LOAD_WAIT_MS = 1500;

function loadAgGrid() {
  if (window.__agGridPromise) return window.__agGridPromise;
  window.__agGridPromise = new Promise((resolve, reject) => {
    if (window.agGrid?.createGrid) return resolve(window.agGrid);
    for (const href of [`${AG_BASE}/styles/ag-grid.css`, `${AG_BASE}/styles/ag-theme-quartz.css`]) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      document.head.appendChild(link);
    }
    const timer = setTimeout(() => reject(new Error("ag-grid timeout")), 8000);
    const url = `${AG_BASE}/dist/ag-grid-community.min.js`;
    // Monaco's AMD loader installs a global `define` (define.amd). AG Grid's UMD bundle sees it
    // and registers as an anonymous AMD module instead of setting window.agGrid — and Monaco
    // can install it at ANY moment while AG Grid is still downloading. So evaluate AG Grid's
    // code with `define`/`exports`/`module` shadowed: its UMD wrapper then always takes the
    // global branch, regardless of load order. If evaluation is blocked (a strict CSP), fall
    // back to a <script> tag with `define` hidden for its duration.
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((code) => {
        // eslint-disable-next-line no-new-func
        new Function("define", "exports", "module", `${code}\n//# sourceURL=${url}`)();
        if (!window.agGrid?.createGrid) throw new Error("ag-grid missing");
        clearTimeout(timer);
        resolve(window.agGrid);
      })
      .catch(() => loadWithScriptTag(url, timer, resolve, reject));
  });
  return window.__agGridPromise;
}

function loadWithScriptTag(url, timer, resolve, reject) {
  const amdDefine = window.define && window.define.amd ? window.define : null;
  if (amdDefine) window.define = undefined;
  const restoreAmd = () => {
    if (amdDefine && window.define === undefined) window.define = amdDefine;
  };
  const script = document.createElement("script");
  script.src = url;
  script.onload = () => {
    clearTimeout(timer);
    restoreAmd();
    window.agGrid?.createGrid ? resolve(window.agGrid) : reject(new Error("ag-grid missing"));
  };
  script.onerror = () => {
    clearTimeout(timer);
    restoreAmd();
    reject(new Error("ag-grid failed"));
  };
  document.head.appendChild(script);
}

/** Start loading AG Grid early (e.g. when the SQL Editor opens) so the first result is instant. */
export function preloadDataGrid() {
  loadAgGrid().catch(() => {});
}

const PAGE_SIZES = [50, 100, 250, 500, 1000, 0]; // 0 = all rows
const FIT_SLACK = 1.15; // shrink-to-fit when columns overflow the grid by at most 15%
const DEFAULT_PAGE_SIZE = 100;
const PAGE_SIZE_KEY = "dbadmin.grid-page-size";
const WRAP_KEY = "dbadmin.grid-wrap";
const fmt = (n) => Number(n).toLocaleString();

function readPref(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch {
    return fallback;
  }
}
function writePref(key, value) {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* ignore */
  }
}

// AG Grid theme class matching the app theme; every rendered grid follows the toggle live.
function agThemeClass() {
  return isDarkTheme() ? "ag-theme-quartz-dark" : "ag-theme-quartz";
}
window.addEventListener(THEME_EVENT, () => {
  document.querySelectorAll("data-grid .ag-host").forEach((host) => {
    host.className = `ag-host ${agThemeClass()}`;
  });
});

export class DataGrid extends HTMLElement {
  connectedCallback() {
    if (this._initialized) return;
    this._initialized = true;
    this.classList.add("data-grid");
    this._columns = [];
    this._rows = [];
    this._meta = {};
    this._page = 0;
    this._sort = { index: null, dir: "asc" };
    this._wrap = readPref(WRAP_KEY, "0") === "1";
    const saved = Number(readPref(PAGE_SIZE_KEY, DEFAULT_PAGE_SIZE));
    this._pageSize = PAGE_SIZES.includes(saved) ? saved : DEFAULT_PAGE_SIZE;
    this._renderToken = 0;
    this.innerHTML = `<div class="rg-state muted">No results yet.</div>`;
  }

  disconnectedCallback() {
    // AG Grid keeps listeners and row models alive until destroyed.
    this._grid?.destroy?.();
    this._grid = null;
  }

  // <data-grid server-paged>: rows are one server page; the owner renders the pager.
  get serverPaged() {
    return this.hasAttribute("server-paged");
  }

  // Show/hide a non-blocking processing overlay over the grid.
  setBusy(on, text = "Loading…") {
    let overlay = this.querySelector(":scope > .grid-busy");
    if (on) {
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.className = "grid-busy";
        this.appendChild(overlay);
      }
      overlay.innerHTML = `<div class="spinner"></div><div class="muted">${escapeCell(text)}</div>`;
    } else {
      overlay?.remove();
    }
  }

  setData(columns, rows, meta = {}) {
    if (!this._initialized) this.connectedCallback();
    this._columns = (columns || []).map((c) => ({ name: c.name, kind: c.kind ?? null }));
    this._rows = rows || [];
    this._meta = meta || {};
    this._page = 0;
    this._sort = { index: null, dir: "asc" };
    this._widths = this._columns.map((c, i) =>
      estimateColumnWidth(c.name, this._rows.map((r) => r[i]), c.kind)
    );
    const token = ++this._renderToken;
    if (window.agGrid?.createGrid) {
      this._renderAg();
      return;
    }
    // First result on this page: give AG Grid a moment to load (it's vendored locally in
    // production, so this is usually instant) rather than flashing a different renderer.
    this.setBusy(true, "Preparing results…");
    const timeout = new Promise((r) => setTimeout(() => r(null), FIRST_LOAD_WAIT_MS));
    Promise.race([loadAgGrid().catch(() => null), timeout]).then((ag) => {
      if (token !== this._renderToken || !this.isConnected) return; // newer data arrived
      if (ag) this._renderAg();
      else this._renderFallback();
    });
  }

  _kindTitle(c) {
    if (c.kind) return `${c.name} · ${kindLabel(c.kind)}`;
    return this._rows.length ? `${c.name} · every value is NULL` : c.name;
  }

  // --- AG Grid --------------------------------------------------------------------------------

  _columnDefs() {
    const offset = Number(this._meta.rowOffset) || 0;
    const digits = String(offset + Math.max(this._rows.length, 1)).length;
    const rowNumber = {
      headerName: "#",
      colId: "__rn",
      valueGetter: (p) => (p.node.rowIndex ?? 0) + 1 + offset,
      width: Math.max(44, digits * 8 + 24),
      pinned: "left",
      lockPosition: "left",
      sortable: false,
      filter: false,
      resizable: false,
      suppressMovable: true,
      suppressSizeToFit: true,
      suppressNavigable: true,
      cellClass: "rg-rownum",
      headerClass: "rg-rownum-h",
    };
    const defs = this._columns.map((c, i) => {
      const numeric = isNumericKind(c.kind);
      return {
        headerName: c.name,
        colId: `c${i}`,
        field: `c${i}`,
        width: this._widths[i],
        minWidth: Math.min(this._widths[i], headerMinWidth(c.name, c.kind)), // never clip the name
        headerTooltip: this._kindTitle(c),
        // No rows → the kind is unknown (not "all NULL"): show no kind label at all.
        headerClass: `rg-h kind-${c.kind || (this._rows.length ? "none" : "unknown")}${numeric ? " rg-h-num" : ""}`,
        cellClass: numeric ? "rg-cell rg-num" : "rg-cell",
        sortable: true,
        resizable: true,
        filter: numeric ? "agNumberColumnFilter" : "agTextColumnFilter",
        filterValueGetter: numeric ? (p) => (p.data[`c${i}`] == null ? null : Number(p.data[`c${i}`])) : undefined,
        comparator: (a, b) => compareCells(a, b, c.kind),
        cellRenderer: (p) => cellHtml(p.value, c.kind),
        tooltipValueGetter: (p) => formatCell(p.value, c.kind).title,
        wrapText: this._wrap,
        autoHeight: this._wrap,
      };
    });
    return [rowNumber, ...defs];
  }

  async _renderAg() {
    let agGrid;
    try {
      agGrid = await loadAgGrid();
    } catch {
      this._renderFallback();
      return;
    }
    this._grid?.destroy?.();
    this._grid = null;
    this.innerHTML = `
      <div class="rg">
        <div class="rg-body"><div class="ag-host ${agThemeClass()}"></div></div>
        <div class="rg-footer"></div>
      </div>`;
    const host = this.querySelector(".ag-host");
    const rowData = this._rows.map((r) => {
      const obj = {};
      for (let i = 0; i < this._columns.length; i++) obj[`c${i}`] = r[i];
      return obj;
    });
    const all = this._pageSize === 0;
    this._grid = agGrid.createGrid(host, {
      columnDefs: this._columnDefs(),
      rowData,
      pagination: !this.serverPaged && !all,
      paginationPageSize: all ? 100 : this._pageSize,
      suppressPaginationPanel: true, // our footer renders the pager
      rowHeight: 32,
      headerHeight: 46,
      rowBuffer: 15,
      animateRows: false,
      rowSelection: "single",
      enableCellTextSelection: true, // select + Ctrl/Cmd+C copies values
      ensureDomOrder: true,
      tooltipShowDelay: 400,
      overlayNoRowsTemplate:
        '<div class="rg-overlay"><strong>No rows returned</strong><span>The query succeeded but the result set is empty.</span></div>',
      onPaginationChanged: () => this._renderFooter(),
      onFilterChanged: () => this._renderFooter(),
      onFirstDataRendered: () => this._fitIfNarrow(),
      onGridSizeChanged: () => this._fitIfNarrow(),
      // Once the user drags a column edge, keep their widths (don't re-fit on resize).
      onColumnResized: (e) => {
        if (e.finished && e.source === "uiColumnResized") this._userResized = true;
      },
    });
    this._userResized = false;
    this._renderFooter();
  }

  // Columns are sized to their content. If they don't fill the grid, stretch them (no empty
  // band on the right); if they overflow only slightly (≤ FIT_SLACK), shrink them to fit rather
  // than add a scrollbar for a few pixels. Wider results scroll horizontally.
  _fitIfNarrow() {
    const api = this._grid;
    const host = this.querySelector(".ag-host");
    if (!api || !host || this._userResized) return;
    const total = api.getColumns()?.reduce((sum, col) => sum + col.getActualWidth(), 0) || 0;
    const avail = host.clientWidth - 2;
    if (total && avail > 0 && total <= avail * FIT_SLACK) api.sizeColumnsToFit();
  }

  // Same rule for the built-in table (scales the <col> widths).
  _fitFallbackWidths() {
    const body = this.querySelector(".rg-body");
    const cols = this.querySelectorAll("colgroup col");
    if (!body || !cols.length) return;
    const widths = [...cols].map((c) => parseFloat(c.style.width) || 0);
    const rowNum = widths[0];
    const data = widths.slice(1).reduce((a, b) => a + b, 0);
    const avail = body.clientWidth - rowNum - 2;
    if (!data || avail <= 0 || data > avail * FIT_SLACK) return;
    const scale = avail / data;
    cols.forEach((c, i) => {
      if (i === 0) return;
      const col = this._columns[i - 1];
      const min = Math.min(widths[i], headerMinWidth(col.name, col.kind)); // never clip the name
      c.style.width = `${Math.max(min, Math.floor(widths[i] * scale))}px`;
    });
  }

  // --- built-in fallback table ------------------------------------------------------------------

  _renderFallback() {
    this._grid?.destroy?.();
    this._grid = null;
    const total = this._rows.length;
    let rows = this._rows;
    if (this._sort.index !== null) {
      const kind = this._columns[this._sort.index]?.kind;
      const idx = this._sort.index;
      const mult = this._sort.dir === "desc" ? -1 : 1;
      rows = kind !== undefined
        ? [...rows].sort((a, b) => compareCells(a[idx], b[idx], kind) * mult)
        : sortRows(rows, idx, this._sort.dir);
    }
    const size = this.serverPaged || this._pageSize === 0 ? Math.max(total, 1) : this._pageSize;
    const pages = pageCount(total, size);
    this._page = Math.min(this._page, pages - 1);
    const pageRows = paginate(rows, this._page, size);
    const offset = (Number(this._meta.rowOffset) || 0) + this._page * size;

    const cols = `<col style="width:${Math.max(44, String(offset + pageRows.length).length * 8 + 24)}px">` +
      this._widths.map((w) => `<col style="width:${w}px">`).join("");
    const head = this._columns
      .map((c, i) => {
        const sorted = this._sort.index === i ? (this._sort.dir === "asc" ? "▲" : "▼") : "";
        const num = isNumericKind(c.kind) ? " rg-h-num" : "";
        const kindText = c.kind || this._rows.length ? kindLabel(c.kind) : "";
        return `<th class="rg-th${num}" data-i="${i}" title="${escapeCell(this._kindTitle(c))}">
          <span class="rg-th-name">${escapeCell(c.name)}</span><span class="rg-kind">${escapeCell(kindText)}</span>
          <span class="rg-sort">${sorted}</span><span class="col-resize" data-rz="${i}"></span></th>`;
      })
      .join("");
    const body = pageRows
      .map((r, n) =>
        `<tr><td class="rg-rownum">${offset + n + 1}</td>${this._columns
          .map((c, i) => {
            const f = formatCell(r[i], c.kind);
            const title = f.title ? ` title="${escapeCell(f.title)}"` : "";
            return `<td class="${isNumericKind(c.kind) ? "rg-num" : ""}"${title}><span class="${f.cls}">${escapeCell(f.text)}</span></td>`;
          })
          .join("")}</tr>`
      )
      .join("");
    const empty = total === 0
      ? `<div class="rg-overlay rg-overlay-inline"><strong>No rows returned</strong><span>The query succeeded but the result set is empty.</span></div>`
      : "";

    this.innerHTML = `
      <div class="rg">
        <div class="rg-body rg-scroll">
          <table class="rg-table${this._wrap ? " wrap" : ""}">
            <colgroup>${cols}</colgroup>
            <thead><tr><th class="rg-rownum">#</th>${head}</tr></thead>
            <tbody>${body}</tbody>
          </table>
          ${empty}
        </div>
        <div class="rg-footer"></div>
      </div>`;

    this.querySelectorAll("th[data-i]").forEach((th) =>
      th.addEventListener("click", (e) => {
        if (e.target.closest(".col-resize")) return;
        this._toggleSort(Number(th.dataset.i));
      })
    );
    this.querySelectorAll(".col-resize").forEach((handle) => {
      handle.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const i = Number(handle.dataset.rz);
        const col = this.querySelectorAll("colgroup col")[i + 1];
        const startX = e.pageX;
        const startW = this._widths[i];
        const onMove = (ev) => {
          this._widths[i] = Math.max(60, startW + ev.pageX - startX);
          col.style.width = `${this._widths[i]}px`;
        };
        const onUp = () => {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    });
    // Click a row to select it (same as the AG Grid renderer).
    this.querySelector("tbody")?.addEventListener("click", (e) => {
      const tr = e.target.closest("tr");
      if (!tr) return;
      this.querySelectorAll("tbody tr.selected").forEach((r) => r !== tr && r.classList.remove("selected"));
      tr.classList.toggle("selected");
    });
    this._fallbackPages = pages;
    this._fitFallbackWidths();
    this._renderFooter();
  }

  _toggleSort(index) {
    if (this._sort.index === index) {
      this._sort.dir = this._sort.dir === "asc" ? "desc" : "asc";
    } else {
      this._sort = { index, dir: "asc" };
    }
    this._page = 0;
    this._renderFallback();
  }

  // --- footer: count, tools, pager ---------------------------------------------------------------

  _pagerState() {
    const api = this._grid;
    const total = this._rows.length;
    if (api) {
      const shown = api.getDisplayedRowCount(); // after column filters
      const paged = api.getGridOption("pagination");
      const size = paged ? api.paginationGetPageSize() : Math.max(shown, 1);
      const page = paged ? api.paginationGetCurrentPage() : 0;
      const pages = paged ? Math.max(1, api.paginationGetTotalPages()) : 1;
      return { total, shown, page, pages, size };
    }
    const size = this.serverPaged || this._pageSize === 0 ? Math.max(total, 1) : this._pageSize;
    return { total, shown: total, page: this._page, pages: this._fallbackPages || 1, size };
  }

  _renderFooter() {
    const footer = this.querySelector(".rg-footer");
    if (!footer) return;
    const { total, shown, page, pages, size } = this._pagerState();
    const from = shown ? page * size + 1 : 0;
    const to = Math.min(shown, (page + 1) * size);
    const filtered = shown !== total ? ` <span class="rg-chip">filtered from ${fmt(total)}</span>` : "";
    const truncated = this._meta.truncated
      ? ` <span class="rg-chip warn" title="The server returned only the first rows. Narrow the query (WHERE / TOP / LIMIT) to see the rest.">Truncated — first ${fmt(total)} rows</span>`
      : "";
    const unit = this.serverPaged ? " on this page" : "";
    const pager = this.serverPaged
      ? ""
      : `<div class="rg-pager" role="navigation" aria-label="Result pages">
          <label class="rg-pagesize">Rows per page
            <select class="input" data-pagesize aria-label="Rows per page">
              ${PAGE_SIZES.map((n) => `<option value="${n}" ${n === this._pageSize ? "selected" : ""}>${n === 0 ? "All" : n}</option>`).join("")}
            </select>
          </label>
          <span class="rg-range">${shown ? `${fmt(from)}–${fmt(to)} of ${fmt(shown)}` : "0 rows"}</span>
          <button class="btn btn-ghost rg-nav" data-nav="first" title="First page" aria-label="First page" ${page <= 0 ? "disabled" : ""}>«</button>
          <button class="btn btn-ghost rg-nav" data-nav="prev" title="Previous page" aria-label="Previous page" ${page <= 0 ? "disabled" : ""}>‹</button>
          <span class="rg-page">Page ${fmt(page + 1)} of ${fmt(pages)}</span>
          <button class="btn btn-ghost rg-nav" data-nav="next" title="Next page" aria-label="Next page" ${page >= pages - 1 ? "disabled" : ""}>›</button>
          <button class="btn btn-ghost rg-nav" data-nav="last" title="Last page" aria-label="Last page" ${page >= pages - 1 ? "disabled" : ""}>»</button>
        </div>`;
    footer.innerHTML = `
      <div class="rg-info">
        <span class="rg-count"><strong>${fmt(shown)}</strong> row${shown === 1 ? "" : "s"}${unit} · ${fmt(this._columns.length)} column${this._columns.length === 1 ? "" : "s"}</span>${filtered}${truncated}
      </div>
      <div class="rg-tools">
        <button class="btn btn-ghost rg-tool${this._wrap ? " active" : ""}" data-wrap aria-pressed="${this._wrap}" title="Wrap long text onto multiple lines">↩ Wrap</button>
        <button class="btn btn-ghost rg-tool" data-csv title="Download these rows as CSV" ${total ? "" : "disabled"}>⤓ CSV</button>
      </div>
      ${pager}`;

    footer.querySelector("[data-wrap]").addEventListener("click", () => this._toggleWrap());
    footer.querySelector("[data-csv]").addEventListener("click", () => this._exportCsv());
    footer.querySelector("[data-pagesize]")?.addEventListener("change", (e) => this._setPageSize(Number(e.target.value)));
    footer.querySelectorAll("[data-nav]").forEach((b) => b.addEventListener("click", () => this._navigate(b.dataset.nav)));
  }

  _navigate(where) {
    const api = this._grid;
    if (api) {
      const go = { first: "paginationGoToFirstPage", prev: "paginationGoToPreviousPage",
        next: "paginationGoToNextPage", last: "paginationGoToLastPage" }[where];
      api[go]?.();
      return;
    }
    const pages = this._fallbackPages || 1;
    this._page = { first: 0, prev: this._page - 1, next: this._page + 1, last: pages - 1 }[where];
    this._page = Math.max(0, Math.min(pages - 1, this._page));
    this._renderFallback();
  }

  _setPageSize(n) {
    this._pageSize = PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE;
    writePref(PAGE_SIZE_KEY, this._pageSize);
    const api = this._grid;
    if (api) {
      if (this._pageSize === 0) {
        api.setGridOption("pagination", false);
      } else {
        api.setGridOption("pagination", true);
        api.setGridOption("paginationPageSize", this._pageSize);
      }
      this._renderFooter();
      return;
    }
    this._page = 0;
    this._renderFallback();
  }

  _toggleWrap() {
    this._wrap = !this._wrap;
    writePref(WRAP_KEY, this._wrap ? "1" : "0");
    const api = this._grid;
    if (api) {
      api.setGridOption("columnDefs", this._columnDefs());
      api.resetRowHeights();
      this._renderFooter();
      return;
    }
    this._renderFallback();
  }

  _exportCsv() {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const fileName = `query-result-${stamp}.csv`;
    // Export the rows as the user sees them (current sort + filters), all pages, raw values.
    let rows = this._rows;
    const api = this._grid;
    if (api) {
      rows = [];
      api.forEachNodeAfterFilterAndSort((node) => {
        rows.push(this._columns.map((_, i) => node.data[`c${i}`]));
      });
    } else if (this._sort.index !== null) {
      const idx = this._sort.index;
      const kind = this._columns[idx]?.kind;
      const mult = this._sort.dir === "desc" ? -1 : 1;
      rows = [...rows].sort((a, b) => compareCells(a[idx], b[idx], kind) * mult);
    }
    const csv = toCsv(this._columns.map((c) => c.name), rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fileName;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
}

customElements.define("data-grid", DataGrid);
