// SQL Editor: Monaco editor with multi-statement script execution. "Run" executes the whole
// script (statement by statement); if text is selected, only the selection runs. Results show
// the last result set; a Messages panel reports every statement's outcome (SSMS/DataGrip-style).

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore } from "../../core/session-state.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";
import "../../components/code-editor.js";
import "../../components/data-grid.js";

export class EditorView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="editor-layout">
        <div class="editor-toolbar">
          <session-bar></session-bar>
          <span class="spacer"></span>
          <button class="btn btn-primary" id="run">▶ Run <span class="muted">⌘⏎</span></button>
        </div>
        <code-editor value="SELECT 1;"></code-editor>
        <div class="result-status" id="status"><span class="muted">Ready.</span></div>
        <div class="result-tabs">
          <button class="rtab active" data-rtab="results">Results</button>
          <button class="rtab" data-rtab="messages">Messages</button>
        </div>
        <data-grid id="grid"></data-grid>
        <div class="messages-panel hidden" id="messages"></div>
      </div>`;

    this._editor = this.querySelector("code-editor");
    this._grid = this.querySelector("#grid");
    this._messages = this.querySelector("#messages");
    this._status = this.querySelector("#status");
    this._runBtn = this.querySelector("#run");

    this._runBtn.addEventListener("click", () => this._run());
    this.addEventListener("run", () => this._run());
    this.querySelectorAll(".rtab").forEach((t) =>
      t.addEventListener("click", () => this._selectTab(t.dataset.rtab))
    );
  }

  _selectTab(tab) {
    this.querySelectorAll(".rtab").forEach((t) =>
      t.classList.toggle("active", t.dataset.rtab === tab)
    );
    this._grid.classList.toggle("hidden", tab !== "results");
    this._messages.classList.toggle("hidden", tab !== "messages");
  }

  async _run() {
    const sessionId = sessionStore.getState().sessionId;
    if (!sessionId) {
      bus.emit(Events.TOAST, { message: "Open a session first", kind: "error" });
      return;
    }
    // Run the selection if there is one, otherwise the whole editor.
    const selected = this._editor.getSelectedText().trim();
    const sql = (selected || this._editor.getValue()).trim();
    if (!sql) return;

    this._runBtn.disabled = true;
    this._runBtn.innerHTML = `<span class="spinner sm"></span> Running…`;
    this._status.innerHTML = `<span class="muted">Executing${selected ? " selection" : ""}…</span>`;
    this._selectTab("results");
    this._grid.setBusy(true, `Executing${selected ? " selection" : ""}…`);
    const start = performance.now();
    try {
      const res = await app.api.executeScript(sessionId, sql);
      this._render(res, Math.round(performance.now() - start));
    } catch (err) {
      this._grid.setBusy(false);
      this._status.innerHTML = `<span style="color:var(--danger)">✕ ${escapeHtml(err.message)}</span>`;
    } finally {
      this._runBtn.disabled = false;
      this._runBtn.innerHTML = `▶ Run <span class="muted">⌘⏎</span>`;
    }
  }

  _render(res, ms) {
    const stmts = res.statements || [];
    // Show the last statement that returned rows in the grid.
    const lastRows = [...stmts].reverse().find((s) => s.success && s.returns_rows);
    if (lastRows) {
      this._grid.setData(lastRows.columns, lastRows.rows);
    } else {
      this._grid.setData([], []);
    }

    // Messages: one line per statement.
    this._messages.innerHTML = stmts
      .map((s, i) => this._messageLine(s, i + 1))
      .join("");

    const failed = stmts.find((s) => !s.success);
    const okCount = stmts.filter((s) => s.success).length;
    if (failed) {
      this._status.innerHTML =
        `<span class="badge cat-ddl">error</span> ` +
        `<span style="color:var(--danger)">✕ Statement ${stmts.indexOf(failed) + 1}: ${escapeHtml(failed.error || failed.error_code)}</span>`;
      this._selectTab("messages");
    } else {
      const rowInfo = lastRows ? ` · ${lastRows.row_count} rows` : "";
      this._status.innerHTML = `<span style="color:var(--success)">✓ ${okCount} statement${okCount === 1 ? "" : "s"} in ${ms} ms${rowInfo}</span>`;
      this._selectTab(lastRows ? "results" : "messages");
    }
  }

  _messageLine(s, n) {
    const cat = { read: "cat-read", write: "cat-write", ddl: "cat-ddl" }[s.category] || "";
    const icon = s.success
      ? '<span style="color:var(--success)">✓</span>'
      : '<span style="color:var(--danger)">✕</span>';
    const detail = s.success
      ? s.returns_rows
        ? `${s.row_count} row(s)`
        : `${s.rows_affected ?? 0} affected`
      : escapeHtml(s.error || s.error_code || "failed");
    return `<div class="msg-line">
      ${icon} <span class="muted">#${n}</span>
      <span class="badge ${cat}">${escapeHtml(s.category)}</span>
      <code class="msg-sql">${escapeHtml(s.sql.slice(0, 90))}${s.sql.length > 90 ? "…" : ""}</code>
      <span class="spacer"></span>
      <span class="${s.success ? "muted" : ""}" style="${s.success ? "" : "color:var(--danger)"}">${detail}</span>
    </div>`;
  }
}

customElements.define("editor-view", EditorView);
