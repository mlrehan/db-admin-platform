// SQL Editor: Monaco editor with multi-statement script execution. "Run" executes the whole
// script (statement by statement); if text is selected, only the selection runs. Results show
// the last result set; a Messages panel reports every statement's outcome (SSMS/DataGrip-style).

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore } from "../../core/session-state.js";
import { clearMetadataCache } from "../../core/metadata-cache.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";
import "../../components/code-editor.js";
import "../../components/data-grid.js";

// Guard against accidentally loading a huge dump into the in-browser editor.
const MAX_SQL_FILE_BYTES = 8 * 1024 * 1024; // 8 MB

export class EditorView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="editor-layout">
        <div class="editor-toolbar">
          <session-bar></session-bar>
          <span class="spacer"></span>
          <button class="btn btn-ghost" id="open-file" title="Open a .sql or .txt file">📂 Open file</button>
          <button class="btn btn-ghost" id="save-file" title="Download the editor contents as a .sql file">💾 Save .sql</button>
          <button class="btn btn-primary" id="run">▶ Run <span class="muted">⌘⏎</span></button>
          <input type="file" id="file-input" accept=".sql,.txt,text/plain" hidden />
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
    this._fileInput = this.querySelector("#file-input");

    this._runBtn.addEventListener("click", () => this._run());
    this.addEventListener("run", () => this._run());
    this.querySelector("#open-file").addEventListener("click", () => this._fileInput.click());
    this.querySelector("#save-file").addEventListener("click", () => this._saveFile());
    this._fileInput.addEventListener("change", (e) => this._openFile(e.target.files[0]));
    this.querySelectorAll(".rtab").forEach((t) =>
      t.addEventListener("click", () => this._selectTab(t.dataset.rtab))
    );
  }

  async _openFile(file) {
    if (!file) return;
    if (file.size > MAX_SQL_FILE_BYTES) {
      bus.emit(Events.TOAST, {
        message: `File is too large (max ${MAX_SQL_FILE_BYTES / (1024 * 1024)} MB).`,
        kind: "error",
      });
      this._fileInput.value = "";
      return;
    }
    try {
      const text = await file.text();
      this._editor.setValue(text);
      this._status.innerHTML = `<span class="muted">Loaded <strong>${escapeHtml(
        file.name
      )}</strong> — press Run to execute.</span>`;
    } catch (err) {
      bus.emit(Events.TOAST, { message: `Could not read file: ${err.message}`, kind: "error" });
    } finally {
      // Allow re-selecting the same file again later.
      this._fileInput.value = "";
    }
  }

  _saveFile() {
    const sql = this._editor.getValue();
    if (!sql.trim()) {
      bus.emit(Events.TOAST, { message: "Nothing to save — the editor is empty.", kind: "error" });
      return;
    }
    // Build a timestamped filename and trigger a client-side download (no server round-trip).
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const blob = new Blob([sql], { type: "application/sql;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `query-${stamp}.sql`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    bus.emit(Events.TOAST, { message: `Saved ${a.download}`, kind: "success" });
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
      // The script may have created/dropped/altered objects — drop cached metadata so the
      // Schema Explorer, Data Viewer and database list show the updated structure immediately.
      if (res.statements?.some((s) => s.success && s.category === "ddl")) {
        clearMetadataCache();
        bus.emit(Events.METADATA_CHANGED, { sessionId });
      }
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
      // A permission denial is the most actionable error — surface it as a toast too so the
      // user clearly sees they're not allowed to run that kind of statement.
      if (failed.error_code === "ACCESS_DENIED") {
        bus.emit(Events.TOAST, { message: failed.error || "Not permitted", kind: "error" });
      }
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
