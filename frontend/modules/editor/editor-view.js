// SQL Editor: Monaco editor with multi-statement script execution. "Run" executes the whole
// script (statement by statement); if text is selected, only the selection runs. Results show
// the last result set; a Messages panel reports every statement's outcome (SSMS/DataGrip-style).

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import {
  sessionStore,
  setActiveDatabase,
  takePendingEditorSql,
} from "../../core/session-state.js";
import { clearMetadataCache } from "../../core/metadata-cache.js";
import { loadCatalog } from "../../core/catalog-store.js";

function newQueryId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  // RFC 4122 v4 fallback for older browsers / non-secure contexts.
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
    (c ^ (Math.random() * 16) >> (c / 4)).toString(16)
  );
}
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";
import "../../components/code-editor.js";
import "../../components/data-grid.js";

// Guard against accidentally loading a huge dump into the in-browser editor.
const MAX_SQL_FILE_BYTES = 8 * 1024 * 1024; // 8 MB

// The current editor text is persisted (per user) so an unsaved query survives navigation and
// page reloads. Keying by the signed-in user's id keeps drafts private: one user never sees
// another user's saved query, even on a shared browser.
const DRAFT_KEY_PREFIX = "dbadmin.sql-draft:";
// Persisted height (px) of the Monaco editor pane above the editor/results divider.
const SPLIT_KEY = "dbadmin.editor-split";

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
        <div class="editor-divider" id="divider" title="Drag to resize editor / results"
             role="separator" aria-orientation="horizontal" tabindex="0"></div>
        <div class="result-status" id="status"><span class="muted">Ready.</span></div>
        <div class="result-tabs">
          <button class="rtab active" data-rtab="results">Results</button>
          <button class="rtab" data-rtab="messages">Messages</button>
        </div>
        <div class="results-area" id="results">
          <div class="grid-empty muted">No results yet.</div>
        </div>
        <div class="messages-panel hidden" id="messages"></div>
      </div>`;

    this._editor = this.querySelector("code-editor");
    this._results = this.querySelector("#results");
    this._messages = this.querySelector("#messages");
    this._status = this.querySelector("#status");
    this._runBtn = this.querySelector("#run");
    this._fileInput = this.querySelector("#file-input");

    // A one-shot hand-off (e.g. "Open in SQL Editor" from the object-definition viewer) takes
    // priority over the saved draft; otherwise restore this user's most recent unsaved query.
    const pending = takePendingEditorSql();
    try {
      if (pending != null) {
        this._editor.setValue(pending);
        this._saveDraft();
      } else {
        const draft = localStorage.getItem(this._draftKey());
        if (draft !== null) this._editor.setValue(draft);
      }
    } catch {
      /* storage unavailable — fall back to the default query */
    }
    // Persist the draft on every edit (debounced).
    this.addEventListener("editor-change", () => this._saveDraft());

    this._runBtn.addEventListener("click", () => (this._running ? this._cancel() : this._run()));
    // Warm the IntelliSense catalog for the active session/database (and on every switch), so
    // the first suggestion list already contains the tables.
    const prefetch = () => {
      const { sessionId, database } = sessionStore.getState();
      if (sessionId) loadCatalog(sessionId, database).catch(() => {});
    };
    this._unsubCatalog = sessionStore.subscribe(prefetch);
    this._unsubCatalogMeta = bus.on(Events.METADATA_CHANGED, prefetch);
    prefetch();
    this.addEventListener("run", () => this._run());
    this.querySelector("#open-file").addEventListener("click", () => this._fileInput.click());
    this.querySelector("#save-file").addEventListener("click", () => this._saveFile());
    this._fileInput.addEventListener("change", (e) => this._openFile(e.target.files[0]));
    this.querySelectorAll(".rtab").forEach((t) =>
      t.addEventListener("click", () => this._selectTab(t.dataset.rtab))
    );
    this._initDivider();
  }

  // Draggable divider between the editor and the results/messages section. Persists the split
  // so it survives reloads; calls Monaco's layout() and lets the grid reflow (flex) after a drag.
  _initDivider() {
    const divider = this.querySelector("#divider");
    const layout = this.querySelector(".editor-layout");
    const MIN_EDITOR = 90;
    const MIN_RESULTS = 140;

    const applyHeight = (px) => {
      const max = layout.clientHeight - MIN_RESULTS;
      const h = Math.max(MIN_EDITOR, Math.min(px, max));
      this._editor.style.height = `${h}px`;
      this._editor.style.flex = "none";
      this._editor.layout?.();
    };
    // Restore a saved split.
    try {
      const saved = Number(localStorage.getItem(SPLIT_KEY));
      if (Number.isFinite(saved) && saved > 0) applyHeight(saved);
    } catch {
      /* ignore */
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
          localStorage.setItem(SPLIT_KEY, String(this._editor.clientHeight));
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
    // Keyboard accessibility: arrow keys nudge the split.
    divider.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowUp" ? -24 : e.key === "ArrowDown" ? 24 : 0;
      if (step) {
        e.preventDefault();
        applyHeight(this._editor.clientHeight + step);
        try {
          localStorage.setItem(SPLIT_KEY, String(this._editor.clientHeight));
        } catch {
          /* ignore */
        }
      }
    });
  }

  disconnectedCallback() {
    // Flush any pending draft immediately when leaving the editor.
    clearTimeout(this._draftTimer);
    this._writeDraft();
    // Release store/bus subscriptions so a closed editor isn't kept alive (no leaks).
    this._unsubCatalog?.();
    this._unsubCatalogMeta?.();
  }

  _saveDraft() {
    clearTimeout(this._draftTimer);
    this._draftTimer = setTimeout(() => this._writeDraft(), 400);
  }

  _draftKey() {
    const uid = app.auth?.user?.id || app.auth?.user?.email || "anon";
    return DRAFT_KEY_PREFIX + uid;
  }

  _writeDraft() {
    try {
      localStorage.setItem(this._draftKey(), this._editor.getValue());
    } catch {
      /* storage full / unavailable — non-fatal */
    }
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
    this._results.classList.toggle("hidden", tab !== "results");
    this._messages.classList.toggle("hidden", tab !== "messages");
  }

  // Show/clear a centered busy overlay while a script runs (the single grid used to own this).
  _setBusy(on, text = "Executing…") {
    if (on) {
      this._results.innerHTML =
        `<div class="grid-busy" style="position:static;flex:1">` +
        `<div class="spinner"></div><div class="muted">${escapeHtml(text)}</div></div>`;
    }
  }

  // A row-resize handle placed between two stacked result-set grids. Dragging it grows/shrinks
  // the block above (the results pane scrolls, so the one below simply moves).
  _makeResultDivider(aboveBlock) {
    const d = document.createElement("div");
    d.className = "result-divider";
    d.title = "Drag to resize the result above";
    d.setAttribute("role", "separator");
    d.setAttribute("aria-orientation", "horizontal");
    const MIN = 80;
    let startY = 0;
    let startH = 0;
    let dragging = false;
    const onMove = (e) => {
      if (!dragging) return;
      const point = e.touches ? e.touches[0].clientY : e.clientY;
      aboveBlock.style.height = `${Math.max(MIN, startH + (point - startY))}px`;
      e.preventDefault();
    };
    const onUp = () => {
      dragging = false;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.userSelect = "";
    };
    d.addEventListener("pointerdown", (e) => {
      dragging = true;
      startY = e.clientY;
      startH = aboveBlock.getBoundingClientRect().height;
      document.body.style.userSelect = "none";
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      e.preventDefault();
    });
    return d;
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

    if (this._running) return; // one run at a time per editor (the session is serialized)
    // A client-generated id lets the user cancel this run while it is still executing.
    const queryId = newQueryId();
    this._running = { queryId };
    this._runBtn.classList.remove("btn-primary");
    this._runBtn.classList.add("btn-danger");
    this._runBtn.innerHTML = `<span class="spinner sm"></span> Cancel`;
    this._runBtn.title = "Cancel the running query";
    this._status.innerHTML = `<span class="muted">Executing${selected ? " selection" : ""}…</span>`;
    this._selectTab("results");
    this._setBusy(true, `Executing${selected ? " selection" : ""}…`);
    const start = performance.now();
    try {
      const res = await app.api.executeScript(sessionId, sql, undefined, { queryId });
      this._render(res, Math.round(performance.now() - start));
      // The script may have created/dropped/altered objects — drop cached metadata so the
      // Schema Explorer, Data Viewer, diagram, database list and IntelliSense refresh.
      if (res.statements?.some((s) => s.category === "ddl")) {
        clearMetadataCache();
        bus.emit(Events.METADATA_CHANGED, { sessionId });
      }
      // A USE in the script switched the session's database (SSMS updates its selector).
      if (res.database_changed && res.active_database) {
        setActiveDatabase(res.active_database);
        bus.emit(Events.METADATA_CHANGED, { sessionId, database: res.active_database });
        bus.emit(Events.TOAST, { message: `Database context changed to "${res.active_database}"`, kind: "info" });
      }
      this._showTransactionState(!!res.open_transaction);
    } catch (err) {
      this._results.innerHTML = `<div class="grid-empty muted">—</div>`;
      this._status.innerHTML = `<span style="color:var(--danger)">✕ ${escapeHtml(err.message)}</span>`;
    } finally {
      this._running = null;
      this._runBtn.disabled = false;
      this._runBtn.classList.remove("btn-danger");
      this._runBtn.classList.add("btn-primary");
      this._runBtn.title = "Run (Ctrl+Enter)";
      this._runBtn.innerHTML = `▶ Run <span class="muted">⌘⏎</span>`;
    }
  }

  // Cancel the in-flight run: the server cancels the statement at the database (ODBC SQLCancel /
  // pg_cancel_backend / KILL QUERY / sqlite interrupt) and the run resolves as "cancelled".
  async _cancel() {
    const running = this._running;
    if (!running || running.cancelling) return;
    running.cancelling = true;
    this._runBtn.disabled = true;
    this._runBtn.innerHTML = `<span class="spinner sm"></span> Cancelling…`;
    try {
      await app.api.cancelQuery(running.queryId);
    } catch {
      /* already finished — the run's own response arrives anyway */
    }
  }

  // A transaction left open (BEGIN TRAN without COMMIT/ROLLBACK) is easy to forget and holds
  // locks — show a persistent warning chip until a later run ends it.
  _showTransactionState(open) {
    let chip = this.querySelector("#tx-chip");
    if (!open) {
      chip?.remove();
      return;
    }
    if (!chip) {
      chip = document.createElement("span");
      chip.id = "tx-chip";
      chip.className = "badge tx-open";
      chip.title = "A transaction is open on this session. Run COMMIT or ROLLBACK.";
      chip.textContent = "⚠ Open transaction";
      this.querySelector(".editor-toolbar .spacer")?.after(chip);
    }
  }

  _render(res, ms) {
    const stmts = res.statements || [];
    // Render EVERY result set the script produced, stacked like Microsoft SSMS. A single set
    // fills the pane; multiple sets each get a bounded, individually-scrollable block labelled
    // "Result N". Result sets produced before a mid-script error are still shown (SSMS-style).
    const resultSets = stmts.filter((s) => s.success && s.returns_rows);
    this._results.innerHTML = "";
    if (resultSets.length) {
      const multi = resultSets.length > 1;
      resultSets.forEach((s, i) => {
        const block = document.createElement("div");
        block.className = `result-block ${multi ? "multi" : "single"}`;
        if (multi) {
          const label = document.createElement("div");
          label.className = "result-label";
          label.innerHTML =
            `<strong>Result ${i + 1}</strong>` +
            `<span class="muted">· ${s.row_count} row${s.row_count === 1 ? "" : "s"}` +
            `${s.truncated ? " (truncated)" : ""}</span>`;
          block.appendChild(label);
        }
        const grid = document.createElement("data-grid");
        block.appendChild(grid);
        this._results.appendChild(block); // connect before setData so it renders
        grid.setData(s.columns, s.rows);
        // A draggable divider between adjacent result sets lets the user resize each grid
        // vertically (SSMS-style), dragging the block above it taller/shorter.
        if (i < resultSets.length - 1) {
          this._results.appendChild(this._makeResultDivider(block));
        }
      });
    } else {
      this._results.innerHTML = `<div class="grid-empty muted">No result set returned.</div>`;
    }

    // Messages: server messages, result summaries and errors (SSMS "Messages" tab).
    this._messages.innerHTML = this._messagesHtml(stmts, ms);

    const failures = stmts.filter((s) => !s.success);
    const failed = failures[0];
    if (failed) {
      // Errors already carry their location (e.g. "Batch 6 (line 21): Msg 137: …").
      const more = failures.length > 1 ? ` <span class="muted">(+${failures.length - 1} more — see Messages)</span>` : "";
      this._status.innerHTML =
        `<span class="badge cat-ddl">${failed.error_code === "QUERY_CANCELLED" ? "cancelled" : "error"}</span> ` +
        `<span style="color:var(--danger)">✕ ${escapeHtml(failed.error || failed.error_code)}</span>${more}`;
      // If the script still produced result sets before failing, keep them visible (SSMS shows
      // the results plus the error). Otherwise focus the Messages tab.
      this._selectTab(resultSets.length ? "results" : "messages");
      // A permission denial is the most actionable error — surface it as a toast too so the
      // user clearly sees they're not allowed to run that kind of statement.
      if (failed.error_code === "ACCESS_DENIED") {
        bus.emit(Events.TOAST, { message: failed.error || "Not permitted", kind: "error" });
      }
    } else {
      const totalRows = resultSets.reduce((n, s) => n + s.row_count, 0);
      const rowInfo = resultSets.length
        ? ` · ${resultSets.length} result set${resultSets.length === 1 ? "" : "s"}, ${totalRows} row${totalRows === 1 ? "" : "s"}`
        : "";
      // The response lists result sets and messages (not statements), so don't present its
      // length as a statement count — "2 statements" for a 3-statement script misled users.
      this._status.innerHTML = `<span style="color:var(--success)">✓ Completed in ${ms} ms${rowInfo}</span>`;
      this._selectTab(resultSets.length ? "results" : "messages");
    }
  }

  // SSMS-style Messages tab: result-set summaries, server messages (row counts, PRINT output,
  // "Changed database context…"), and errors — each error already names its batch/statement.
  _messagesHtml(stmts, ms) {
    let resultNo = 0;
    const lines = stmts.map((s) => {
      if (!s.success) {
        return `<div class="msg-line msg-error"><span>✕</span>
          <pre class="msg-text">${escapeHtml(s.error || s.error_code || "failed")}</pre></div>`;
      }
      if (s.returns_rows) {
        resultNo += 1;
        return `<div class="msg-line"><span style="color:var(--success)">✓</span>
          <span class="msg-text">Result ${resultNo}: ${s.row_count} row(s) returned${s.truncated ? " (truncated)" : ""}</span></div>`;
      }
      // A message outcome carries the server/engine message in `sql`; the lone fallback outcome
      // (nothing else produced) carries the script itself.
      const text = stmts.length === 1 && s.execution_ms > 0 ? "Commands completed successfully." : s.sql;
      return `<div class="msg-line"><span class="muted">›</span>
        <pre class="msg-text">${escapeHtml(text)}</pre></div>`;
    });
    lines.push(`<div class="msg-line msg-footer muted">Completion time: ${escapeHtml(
      new Date().toLocaleString()
    )} · ${ms} ms</div>`);
    return lines.join("");
  }
}

customElements.define("editor-view", EditorView);
