// Browser-Based SQLite Playground View ("/playground").
//
// Provides a standalone, public SQL IDE experience backed by an isolated in-memory SQLite
// engine (`PlaygroundDatabase`). Requires no authentication and never communicates with the
// enterprise backend or database connections.

import { app } from "../../core/context.js";
import { config } from "../../core/config.js";
import { icons } from "../../components/icons.js";
import { escapeHtml } from "../../components/view-helpers.js";
import { makeResizableTable } from "../../components/resizable-table.js";
import { confirm, toast } from "../../core/notify.js";
import { getTheme, toggleTheme } from "../../core/theme.js";
import "../../components/code-editor.js";
import {
  PlaygroundDatabase,
  INITIAL_PLAYGROUND_QUERY,
  SAMPLE_QUERIES,
} from "./sqlite-engine.js";

const SPLIT_STORAGE_KEY = "dbadmin.playground-split";

export class PlaygroundView extends HTMLElement {
  connectedCallback() {
    this._db = new PlaygroundDatabase();
    this._collapsedTables = new Set();
    this._wrapCells = false;
    this._renderShell();
    this._bootDatabase();
  }

  disconnectedCallback() {
    this._db?.close();
  }

  _renderShell() {
    const isDark = getTheme() === "dark";
    const sampleOpts = SAMPLE_QUERIES.map(
      (q) => `<option value="${escapeHtml(q.id)}">${escapeHtml(q.label)}</option>`
    ).join("");

    this.innerHTML = `
      <div class="playground-shell">
        <header class="playground-topbar">
          <button
            type="button"
            class="btn btn-ghost playground-back-btn"
            id="playground-back"
            title="Back to SQL Studio Landing Page"
          >
            ${icons.arrowLeft}
            <span>SQL Studio</span>
          </button>

          <span class="playground-breadcrumb-sep" aria-hidden="true">/</span>

          <div class="playground-title-group">
            <span class="dot"></span>
            <h1 class="playground-title">SQL Playground</h1>
            <span class="badge cat-read playground-engine-badge" title="Runs locally in your browser via SQLite WebAssembly">
              SQLite 3 &middot; In-Browser
            </span>
          </div>

          <span class="spacer"></span>

          <div class="playground-toolbar-actions">
            <label class="playground-sample-label" for="playground-samples">Examples</label>
            <select
              class="input playground-sample-select"
              id="playground-samples"
              aria-label="Load a sample SQL query"
            >
              <option value="">Load sample query…</option>
              ${sampleOpts}
            </select>

            <button
              type="button"
              class="btn btn-primary"
              id="playground-run"
              title="Execute SQL query (Ctrl+Enter or Cmd+Enter)"
            >
              ▶ Run Query <span class="muted playground-shortcut">Ctrl+⏎</span>
            </button>

            <button
              type="button"
              class="btn"
              id="playground-reset"
              title="Restore original schema and sample records"
            >
              ⟳ Reset Database
            </button>

            <button
              type="button"
              class="btn btn-ghost"
              id="playground-theme"
              title="Toggle color theme"
              aria-label="Toggle color theme"
            >
              ${isDark ? icons.sun : icons.moon}
            </button>

            <button
              type="button"
              class="btn btn-ghost playground-workspace-link"
              id="playground-to-workspace"
              title="Sign in to the Enterprise SQL Workspace"
            >
              <span>Enterprise Workspace</span>
              ${icons.arrowRight}
            </button>
          </div>
        </header>

        <div class="playground-workspace">
          <!-- Left Panel: Database Schema -->
          <aside class="playground-sidebar" aria-label="Database Schema">
            <div class="playground-panel-head">
              <span class="playground-panel-title">
                ${icons.schema}
                <span>Database Schema</span>
              </span>
              <span class="badge" id="playground-table-count">3 tables</span>
            </div>
            <div class="playground-schema-tree" id="playground-schema">
              <div class="muted" style="padding:12px">Initializing SQLite schema…</div>
            </div>
            <div class="playground-sidebar-footer">
              <div class="playground-isolation-note">
                <strong>Session-only browser memory</strong>
                <span>Changes stay in your browser and reset on page reload or when you click Reset Database.</span>
              </div>
            </div>
          </aside>

          <!-- Main Panel: SQL Editor + Query Results -->
          <main class="playground-main">
            <div class="playground-editor-head">
              <span class="playground-panel-title">
                ${icons.editor}
                <span>SQL Query Editor</span>
              </span>
              <span class="muted playground-editor-hint">
                Write any SQLite query (<code>SELECT</code>, <code>JOIN</code>, <code>INSERT</code>, <code>UPDATE</code>, <code>DELETE</code>, <code>CREATE TABLE</code>)
              </span>
              <span class="spacer"></span>
              <button
                type="button"
                class="btn btn-ghost playground-clear-btn"
                id="playground-clear-sql"
                title="Clear editor text"
              >
                Clear
              </button>
            </div>

            <code-editor
              id="playground-editor"
              value="${escapeHtml(INITIAL_PLAYGROUND_QUERY)}"
            ></code-editor>

            <div
              class="editor-divider"
              id="playground-divider"
              title="Drag to resize editor and results"
              role="separator"
              aria-orientation="horizontal"
              tabindex="0"
            ></div>

            <div class="playground-results-head">
              <span class="playground-panel-title">
                ${icons.table}
                <span>Query Results</span>
              </span>
              <div class="playground-status" id="playground-status" role="status" aria-live="polite">
                <span class="muted">Loading SQLite engine…</span>
              </div>
              <span class="spacer"></span>
              <button
                type="button"
                class="btn btn-ghost playground-wrap-btn"
                id="playground-wrap"
                title="Toggle text wrapping in result cells"
              >
                ↩ Wrap: off
              </button>
            </div>

            <div class="playground-results-body" id="playground-results">
              <div class="grid-busy" style="position:static;flex:1">
                <div class="spinner"></div>
                <div class="muted">Starting in-browser SQLite engine…</div>
              </div>
            </div>
          </main>
        </div>
      </div>
    `;

    this._editor = this.querySelector("#playground-editor");
    this._schemaEl = this.querySelector("#playground-schema");
    this._tableCountEl = this.querySelector("#playground-table-count");
    this._statusEl = this.querySelector("#playground-status");
    this._resultsEl = this.querySelector("#playground-results");
    this._runBtn = this.querySelector("#playground-run");
    this._resetBtn = this.querySelector("#playground-reset");
    this._sampleSelect = this.querySelector("#playground-samples");
    this._wrapBtn = this.querySelector("#playground-wrap");

    // Navigation actions
    this.querySelector("#playground-back").addEventListener("click", () => {
      app.router?.navigate("/");
    });
    this.querySelector("#playground-to-workspace").addEventListener("click", () => {
      app.router?.navigate("/login");
    });

    // Theme toggle
    const themeBtn = this.querySelector("#playground-theme");
    themeBtn.addEventListener("click", () => {
      const dark = toggleTheme() === "dark";
      themeBtn.innerHTML = dark ? icons.sun : icons.moon;
    });

    // Run & Reset actions
    this._runBtn.addEventListener("click", () => this.runQuery());
    this.addEventListener("run", () => this.runQuery());
    this._resetBtn.addEventListener("click", () => this.confirmAndReset());

    // Clear editor button
    this.querySelector("#playground-clear-sql").addEventListener("click", () => {
      this._editor.setValue("");
      this._editor.focus();
    });

    // Sample query selector
    this._sampleSelect.addEventListener("change", () => {
      const chosen = SAMPLE_QUERIES.find((q) => q.id === this._sampleSelect.value);
      if (chosen) {
        this._editor.setValue(chosen.sql);
        this.runQuery();
      }
      this._sampleSelect.value = "";
    });

    // Wrap toggle
    this._wrapBtn.addEventListener("click", () => {
      this._wrapCells = !this._wrapCells;
      this._wrapBtn.textContent = this._wrapCells ? "↩ Wrap: on" : "↩ Wrap: off";
      this._resultsEl.querySelectorAll(".grid-table").forEach((tbl) => {
        tbl.classList.toggle("wrap", this._wrapCells);
      });
    });

    this._initSplitter();
  }

  async _bootDatabase() {
    this._setControlsBusy(true, "Initializing…");
    try {
      await this._db.init();
      this._renderSchema();
      this._setControlsBusy(false);
      // Run the initial query (`SELECT * FROM student;`) immediately on first open.
      this.runQuery();
    } catch (err) {
      this._setControlsBusy(false);
      const msg = err?.message || "Failed to initialize the browser SQLite engine.";
      this._statusEl.innerHTML = `<span style="color:var(--danger)">✕ ${escapeHtml(msg)}</span>`;
      this._resultsEl.innerHTML = `
        <div class="playground-error-box" role="alert">
          <div class="playground-error-title">SQLite Engine Initialization Error</div>
          <div class="playground-error-msg">${escapeHtml(msg)}</div>
        </div>
      `;
    }
  }

  _setControlsBusy(busy, label = "Running…") {
    if (!this._runBtn || !this._resetBtn) return;
    this._runBtn.disabled = busy;
    this._resetBtn.disabled = busy;
    if (busy) {
      this._runBtn.innerHTML = `<span class="spinner sm"></span> ${escapeHtml(label)}`;
    } else {
      this._runBtn.innerHTML = `▶ Run Query <span class="muted playground-shortcut">Ctrl+⏎</span>`;
    }
  }

  _renderSchema() {
    const tables = this._db.getSchema();
    this._tableCountEl.textContent = `${tables.length} table${tables.length === 1 ? "" : "s"}`;

    if (!tables.length) {
      this._schemaEl.innerHTML = `<div class="muted" style="padding:12px">No tables in database. Click Reset Database to restore the sample tables.</div>`;
      return;
    }

    this._schemaEl.innerHTML = tables
      .map((t) => {
        const collapsed = this._collapsedTables.has(t.name);
        const colsHtml = t.columns
          .map((c) => {
            const pkBadge = c.primaryKey
              ? `<span class="playground-col-badge pk" title="Primary Key">PK</span>`
              : "";
            const fkBadge = c.foreignKey
              ? `<span class="playground-col-badge fk" title="Foreign Key → ${escapeHtml(
                  c.foreignKey.referredTable
                )}.${escapeHtml(c.foreignKey.referredColumn)}">FK → ${escapeHtml(
                  c.foreignKey.referredTable
                )}</span>`
              : "";
            return `
              <li class="playground-col-item">
                <div class="playground-col-main">
                  <code class="playground-col-name">${escapeHtml(c.name)}</code>
                  ${pkBadge}
                  ${fkBadge}
                </div>
                <span class="playground-col-type">${escapeHtml(c.type)}${
              c.notNull && !c.primaryKey ? " NOT NULL" : ""
            }</span>
              </li>
            `;
          })
          .join("");

        return `
          <div class="playground-table-node" data-table="${escapeHtml(t.name)}">
            <div class="playground-table-header" role="button" tabindex="0" aria-expanded="${!collapsed}">
              <span class="tree-caret">${collapsed ? "▸" : "▾"}</span>
              <span class="tree-icon" aria-hidden="true">▤</span>
              <strong class="playground-table-name">${escapeHtml(t.name)}</strong>
              <span class="badge playground-row-badge" title="${t.rowCount} rows">${t.rowCount}</span>
              <button
                type="button"
                class="tree-action playground-quick-select"
                data-select-table="${escapeHtml(t.name)}"
                title="Preview rows: SELECT * FROM ${escapeHtml(t.name)}"
              >
                SELECT
              </button>
            </div>
            <ul class="playground-col-list${collapsed ? " hidden" : ""}">
              ${colsHtml}
            </ul>
          </div>
        `;
      })
      .join("");

    // Wire expand/collapse and quick SELECT buttons
    this._schemaEl.querySelectorAll(".playground-table-node").forEach((node) => {
      const tableName = node.dataset.table;
      const header = node.querySelector(".playground-table-header");
      const list = node.querySelector(".playground-col-list");
      const caret = node.querySelector(".tree-caret");

      const toggle = () => {
        const isHidden = list.classList.toggle("hidden");
        caret.textContent = isHidden ? "▸" : "▾";
        header.setAttribute("aria-expanded", String(!isHidden));
        if (isHidden) this._collapsedTables.add(tableName);
        else this._collapsedTables.delete(tableName);
      };

      header.addEventListener("click", (e) => {
        if (e.target.closest(".playground-quick-select")) return;
        toggle();
      });
      header.addEventListener("keydown", (e) => {
        if (e.target.closest(".playground-quick-select")) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle();
        }
      });
    });

    this._schemaEl.querySelectorAll(".playground-quick-select").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const tbl = btn.dataset.selectTable;
        const ident = /^[A-Za-z_][A-Za-z0-9_]*$/.test(tbl) ? tbl : `"${tbl.replace(/"/g, '""')}"`;
        this._editor.setValue(`SELECT * FROM ${ident};`);
        this.runQuery();
      });
    });
  }

  runQuery() {
    if (!this._db.isReady || this._db.isRunning) return;

    const selected = this._editor.getSelectedText?.().trim() || "";
    const sqlToRun = selected || this._editor.getValue();

    if (!sqlToRun || !sqlToRun.trim()) {
      this._statusEl.innerHTML = `<span style="color:var(--warning)">⚠ Please enter a SQL query to execute.</span>`;
      this._resultsEl.innerHTML = `
        <div class="playground-error-box warning" role="alert">
          <div class="playground-error-title">Empty SQL Query</div>
          <div class="playground-error-msg">Please enter a SQL statement in the editor before clicking Run Query.</div>
        </div>
      `;
      return;
    }

    this._setControlsBusy(true, "Executing…");
    try {
      const outcome = this._db.execute(sqlToRun);
      // Always refresh schema in case DDL or DML changed tables or row counts.
      this._renderSchema();
      this._renderOutcome(outcome);
    } finally {
      this._setControlsBusy(false);
    }
  }

  _renderOutcome(outcome) {
    if (!outcome.ok) {
      if (outcome.empty) {
        this._statusEl.innerHTML = `<span style="color:var(--warning)">⚠ ${escapeHtml(outcome.error)}</span>`;
        this._resultsEl.innerHTML = `
          <div class="playground-error-box warning" role="alert">
            <div class="playground-error-title">Empty SQL Query</div>
            <div class="playground-error-msg">${escapeHtml(outcome.error)}</div>
          </div>
        `;
        return;
      }

      this._statusEl.innerHTML = `<span style="color:var(--danger)">✕ SQL Error (${outcome.durationMs} ms)</span>`;
      this._resultsEl.innerHTML = `
        <div class="playground-error-box" role="alert">
          <div class="playground-error-title">SQL Execution Error</div>
          <div class="playground-error-msg mono">${escapeHtml(outcome.error)}</div>
        </div>
      `;
      return;
    }

    const { resultSets, statements, durationMs, totalRowsReturned, totalRowsAffected } = outcome;

    // Case 1: Statements executed, but none returned a tabular result set (e.g., INSERT, UPDATE, DELETE, CREATE TABLE)
    if (resultSets.length === 0) {
      const affectedLabel =
        totalRowsAffected > 0
          ? `${totalRowsAffected} row${totalRowsAffected === 1 ? "" : "s"} affected`
          : "0 rows affected";
      this._statusEl.innerHTML = `<span style="color:var(--success)">✓ Query executed in ${durationMs} ms &middot; ${affectedLabel}</span>`;

      const stmtLines = statements
        .map(
          (s, i) => `
          <div class="msg-line">
            <span style="color:var(--success)">✓</span>
            <span class="muted">#${i + 1}</span>
            <span class="badge cat-${escapeHtml(s.category)}">${escapeHtml(s.category)}</span>
            <code class="msg-sql">${escapeHtml(s.sql.slice(0, 120))}${
            s.sql.length > 120 ? "…" : ""
          }</code>
            <span class="spacer"></span>
            <span class="muted">${s.rowsAffected} row${
            s.rowsAffected === 1 ? "" : "s"
          } affected</span>
          </div>
        `
        )
        .join("");

      this._resultsEl.innerHTML = `
        <div class="playground-exec-success" role="status">
          <div class="playground-exec-banner">
            <span class="playground-exec-check">✓</span>
            <div>
              <div class="playground-exec-title">Query executed successfully</div>
              <div class="muted">${statements.length} statement${
        statements.length === 1 ? "" : "s"
      } completed in ${durationMs} ms &middot; ${affectedLabel}</div>
            </div>
          </div>
          <div class="playground-exec-log">${stmtLines}</div>
        </div>
      `;
      return;
    }

    // Case 2: One or more result sets returned (e.g., SELECT, PRAGMA, EXPLAIN)
    const rowWord = totalRowsReturned === 1 ? "row" : "rows";
    const multi = resultSets.length > 1;
    const dmlNote =
      totalRowsAffected > 0
        ? ` &middot; ${totalRowsAffected} row${totalRowsAffected === 1 ? "" : "s"} modified`
        : "";
    this._statusEl.innerHTML = `<span style="color:var(--success)">✓ ${totalRowsReturned} ${rowWord} returned in ${durationMs} ms${dmlNote}</span>`;

    this._resultsEl.innerHTML = resultSets
      .map((rs, idx) => {
        const headerCells = rs.columns
          .map((col) => `<th><span class="th-label">${escapeHtml(col)}</span></th>`)
          .join("");

        const bodyRows = rs.rows.length
          ? rs.rows
              .map(
                (row) =>
                  `<tr>${row
                    .map((cell) =>
                      cell === null || cell === undefined
                        ? `<td><span class="null">NULL</span></td>`
                        : `<td>${escapeHtml(cell)}</td>`
                    )
                    .join("")}</tr>`
              )
              .join("")
          : `<tr><td colspan="${Math.max(1, rs.columns.length)}" class="muted" style="padding:16px;text-align:center">No matching rows returned (0 rows).</td></tr>`;

        const multiHeader = multi
          ? `<div class="result-label">
               <strong>Result ${idx + 1}</strong>
               <span class="muted">&middot; ${rs.totalRowCount} row${
              rs.totalRowCount === 1 ? "" : "s"
            }${rs.truncated ? ` (showing first ${rs.rowCount})` : ""}</span>
             </div>`
          : "";

        return `
          <div class="playground-result-set ${multi ? "multi" : "single"}">
            ${multiHeader}
            <div class="grid-scroll">
              <table class="grid-table${this._wrapCells ? " wrap" : ""}">
                <thead><tr>${headerCells}</tr></thead>
                <tbody>${bodyRows}</tbody>
              </table>
            </div>
            <div class="grid-footer">
              <span class="muted">${rs.totalRowCount} row${
          rs.totalRowCount === 1 ? "" : "s"
        } returned${rs.truncated ? ` (truncated to ${rs.rowCount})` : ""}</span>
              <span class="spacer"></span>
              <span class="muted">SQLite 3 &middot; ${durationMs} ms</span>
            </div>
          </div>
        `;
      })
      .join("");

    this._resultsEl.querySelectorAll(".grid-table").forEach((tbl) => {
      makeResizableTable(tbl);
    });
  }

  async confirmAndReset() {
    if (!this._db.isReady || this._db.isRunning) return;

    const confirmed = await confirm({
      title: "Reset Database to Initial State?",
      text: "All changes made in the SQL Playground will be discarded, and the original course, student, and admission tables will be restored.",
      confirmText: "Reset Database",
      danger: true,
    });

    if (!confirmed) return;

    this._setControlsBusy(true, "Resetting…");
    try {
      this._db.reset();
      this._collapsedTables.clear();
      this._renderSchema();
      this._editor.setValue(INITIAL_PLAYGROUND_QUERY);
      const outcome = this._db.execute(INITIAL_PLAYGROUND_QUERY);
      this._renderOutcome(outcome);
      this._statusEl.innerHTML = `<span style="color:var(--success)">✓ Database reset to initial state &middot; ${outcome.totalRowsReturned} rows returned</span>`;
      toast("Playground database reset to initial state", "success");
    } catch (err) {
      const msg = err?.message || "Failed to reset database.";
      this._statusEl.innerHTML = `<span style="color:var(--danger)">✕ ${escapeHtml(msg)}</span>`;
    } finally {
      this._setControlsBusy(false);
    }
  }

  _initSplitter() {
    const divider = this.querySelector("#playground-divider");
    const main = this.querySelector(".playground-main");
    if (!divider || !main || !this._editor) return;

    const MIN_EDITOR = 110;
    const MIN_RESULTS = 150;

    const applyHeight = (px) => {
      const max = Math.max(MIN_EDITOR, main.clientHeight - MIN_RESULTS);
      const h = Math.max(MIN_EDITOR, Math.min(px, max));
      this._editor.style.height = `${h}px`;
      this._editor.style.flex = "none";
      this._editor.layout?.();
    };

    try {
      const saved = Number(localStorage.getItem(SPLIT_STORAGE_KEY));
      if (Number.isFinite(saved) && saved >= MIN_EDITOR) {
        applyHeight(saved);
      }
    } catch {
      /* ignore storage errors */
    }

    const startDrag = (clientY) => {
      const startTop = this._editor.getBoundingClientRect().top;
      const move = (y) => applyHeight(y - startTop);
      const onMouseMove = (e) => move(e.clientY);
      const onTouchMove = (e) => e.touches[0] && move(e.touches[0].clientY);
      const stop = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", stop);
        document.removeEventListener("touchmove", onTouchMove);
        document.removeEventListener("touchend", stop);
        document.body.style.userSelect = "";
        try {
          localStorage.setItem(SPLIT_STORAGE_KEY, String(this._editor.clientHeight));
        } catch {
          /* ignore */
        }
      };
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", stop);
      document.addEventListener("touchmove", onTouchMove, { passive: true });
      document.addEventListener("touchend", stop);
    };

    divider.addEventListener("mousedown", (e) => {
      e.preventDefault();
      startDrag(e.clientY);
    });
    divider.addEventListener("touchstart", (e) => e.touches[0] && startDrag(e.touches[0].clientY), {
      passive: true,
    });
    divider.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowUp" ? -24 : e.key === "ArrowDown" ? 24 : 0;
      if (step) {
        e.preventDefault();
        applyHeight(this._editor.clientHeight + step);
      }
    });
  }
}

if (typeof customElements !== "undefined" && !customElements.get("playground-view")) {
  customElements.define("playground-view", PlaygroundView);
}

