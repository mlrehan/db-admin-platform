// Tabular result grid. Renders with AG Grid when it loads from CDN; otherwise uses a built-in
// sortable, paginated table (fully functional). `setData(columns, rows)` where columns is
// [{name}] and rows is an array of arrays. Phase 9 vendors AG Grid locally.

import { sortRows, paginate, pageCount } from "./grid-utils.js";
import { escapeHtml } from "./view-helpers.js";
import { config } from "../core/config.js";

const AG_BASE = config.agGridBase;

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
    const script = document.createElement("script");
    script.src = `${AG_BASE}/dist/ag-grid-community.min.js`;
    script.onload = () => {
      clearTimeout(timer);
      window.agGrid?.createGrid ? resolve(window.agGrid) : reject(new Error("ag-grid missing"));
    };
    script.onerror = () => {
      clearTimeout(timer);
      reject(new Error("ag-grid failed"));
    };
    document.head.appendChild(script);
  });
  return window.__agGridPromise;
}

const PAGE_SIZE = 100;

export class DataGrid extends HTMLElement {
  connectedCallback() {
    this.classList.add("data-grid");
    this._columns = [];
    this._rows = [];
    this._page = 0;
    this._sort = { index: null, dir: "asc" };
    this.innerHTML = `<div class="grid-empty muted">No results yet.</div>`;
  }

  // Show/hide a non-blocking processing overlay over the grid.
  setBusy(on, text = "Processing…") {
    let overlay = this.querySelector(":scope > .grid-busy");
    if (on) {
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.className = "grid-busy";
        this.appendChild(overlay);
      }
      overlay.innerHTML = `<div class="spinner"></div><div class="muted">${text}</div>`;
    } else {
      overlay?.remove();
    }
  }

  setData(columns, rows) {
    this.setBusy(false);
    this._columns = columns || [];
    this._rows = rows || [];
    this._page = 0;
    this._sort = { index: null, dir: "asc" };
    this._colWidths = {};
    // Render instantly with the built-in grid. If AG Grid is already loaded, use it; either
    // way warm AG Grid in the background so it's ready for subsequent result sets.
    if (window.agGrid?.createGrid) {
      this._renderAg();
    } else {
      this._renderFallback();
      this._warmAgGrid();
    }
  }

  _warmAgGrid() {
    if (this._warming) return;
    this._warming = true;
    loadAgGrid().catch(() => {});
  }

  async _renderAg() {
    try {
      const agGrid = await loadAgGrid();
      this.innerHTML = "";
      const host = document.createElement("div");
      host.className = "ag-theme-quartz-dark";
      host.style.cssText = "width:100%;height:100%;min-height:300px";
      this.appendChild(host);
      const columnDefs = this._columns.map((c, i) => ({
        headerName: c.name,
        field: `c${i}`,
        sortable: true,
        resizable: true,
        filter: true,
      }));
      const rowData = this._rows.map((r) => {
        const obj = {};
        r.forEach((v, i) => (obj[`c${i}`] = v));
        return obj;
      });
      if (this._grid) this._grid.destroy?.();
      this._grid = agGrid.createGrid(host, {
        columnDefs,
        rowData,
        pagination: true,
        paginationPageSize: PAGE_SIZE,
        defaultColDef: { minWidth: 90, flex: 1 },
      });
    } catch {
      this._renderFallback();
    }
  }

  _renderFallback() {
    const total = this._rows.length;
    let rows = this._rows;
    if (this._sort.index !== null) rows = sortRows(rows, this._sort.index, this._sort.dir);
    const pages = pageCount(total, PAGE_SIZE);
    this._page = Math.min(this._page, pages - 1);
    const pageRows = paginate(rows, this._page, PAGE_SIZE);

    const head = this._columns
      .map((c, i) => {
        const arrow =
          this._sort.index === i ? (this._sort.dir === "asc" ? " ▲" : " ▼") : "";
        const w = this._colWidths?.[i] ? ` style="width:${this._colWidths[i]}px"` : "";
        return `<th data-i="${i}"${w}><span class="th-label">${escapeHtml(c.name)}${arrow}</span><span class="col-resize" data-rz="${i}"></span></th>`;
      })
      .join("");
    const body = pageRows
      .map(
        (r) =>
          `<tr>${r
            .map((v) => `<td>${v === null ? '<span class="null">NULL</span>' : escapeHtml(v)}</td>`)
            .join("")}</tr>`
      )
      .join("");

    this.innerHTML = `
      <div class="grid-scroll">
        <table class="grid-table">
          <thead><tr>${head || "<th></th>"}</tr></thead>
          <tbody>${body || `<tr><td class="muted">No rows</td></tr>`}</tbody>
        </table>
      </div>
      <div class="grid-footer">
        <span class="muted">${total} row${total === 1 ? "" : "s"}</span>
        <span class="spacer"></span>
        <button class="btn btn-ghost" data-nav="prev" ${this._page === 0 ? "disabled" : ""}>‹</button>
        <span class="muted">Page ${this._page + 1} / ${pages}</span>
        <button class="btn btn-ghost" data-nav="next" ${
          this._page >= pages - 1 ? "disabled" : ""
        }>›</button>
      </div>`;

    this.querySelectorAll("th[data-i]").forEach((th) =>
      th.querySelector(".th-label").addEventListener("click", () =>
        this._toggleSort(Number(th.dataset.i))
      )
    );
    // Draggable column resize handles.
    this._colWidths = this._colWidths || {};
    this.querySelectorAll(".col-resize").forEach((handle) => {
      handle.addEventListener("click", (e) => e.stopPropagation());
      handle.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const i = Number(handle.dataset.rz);
        const th = handle.closest("th");
        const startX = e.pageX;
        const startW = th.offsetWidth;
        const onMove = (ev) => {
          const w = Math.max(60, startW + ev.pageX - startX);
          th.style.width = `${w}px`;
          this._colWidths[i] = w;
        };
        const onUp = () => {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    });
    this.querySelector('[data-nav="prev"]')?.addEventListener("click", () => {
      this._page = Math.max(0, this._page - 1);
      this._renderFallback();
    });
    this.querySelector('[data-nav="next"]')?.addEventListener("click", () => {
      this._page = Math.min(pages - 1, this._page + 1);
      this._renderFallback();
    });
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
}

customElements.define("data-grid", DataGrid);
