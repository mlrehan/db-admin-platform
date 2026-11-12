// Database Diagram Designer (SSMS-style).
//
// Left: the database's tables (filter; double-click, drag onto the canvas, or the ＋ button to
// add). Right: the canvas — table boxes with columns, PK/FK markers and types; relationship
// connectors anchored to the actual key columns with crow's-foot cardinality (1:1, 1:N, and
// logical M:N through junction tables); drag boxes (multi-select), zoom to cursor, pan, fit,
// auto-layout; drag a column onto another column to create a FOREIGN KEY; remove tables
// (× / Delete / menu); hide connectors or drop the constraint from the database.
//
// Architecture: core/diagram-model.js is the single source of truth. Adding a table reserves its
// node synchronously (so repeated clicks / double-clicks can never create duplicates), and this
// view renders strictly FROM the model — `_sync()` reconciles the DOM against `model.nodes`, so no
// box can exist that the model doesn't know about (the old "can't remove it" orphan bug).
// Relationships come from GET /sessions/{id}/relationships (all FKs + cardinality + junctions),
// cached server-side and refreshed automatically whenever the schema changes.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore, setPendingEditorSql } from "../../core/session-state.js";
import { clearMetadataCache } from "../../core/metadata-cache.js";
import { confirm } from "../../core/notify.js";
import {
  DiagramModel,
  addForeignKeySql,
  defaultConstraintName,
  displayType,
  dropForeignKeySql,
  planRelationship,
  tableKey,
} from "../../core/diagram-model.js";
import { openModal } from "../../components/modal.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";

const PANEL_KEY = "dbadmin.erd-panel-width";
const LAYOUT_PREFIX = "dbadmin.erd-layout:";
const DND_TYPE = "application/x-dbadmin-table";
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 2.5;
const SVG_NS = "http://www.w3.org/2000/svg";

export class DiagramView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="erd-layout">
        <div class="editor-toolbar erd-toolbar">
          <session-bar></session-bar>
          <span class="spacer"></span>
          <button class="btn btn-ghost" data-act="refresh" title="Reload tables and relationships">⟳ Refresh</button>
          <button class="btn btn-ghost" data-act="layout" title="Arrange tables automatically">▦ Auto layout</button>
          <button class="btn btn-ghost" data-act="fit" title="Fit diagram to window">⤢ Fit</button>
          <button class="btn btn-ghost" data-act="zoom-out" title="Zoom out">−</button>
          <button class="btn btn-ghost erd-zoom-label" data-act="zoom-reset" title="Reset zoom">100%</button>
          <button class="btn btn-ghost" data-act="zoom-in" title="Zoom in">+</button>
          <button class="btn btn-ghost btn-danger" data-act="clear" title="Remove every table from the diagram">Clear</button>
        </div>
        <div class="erd-main">
          <div class="erd-list" id="erd-list">
            <div class="erd-list-head">
              <span class="muted">Tables</span>
              <input class="input erd-filter" id="erd-filter" placeholder="Filter…" />
            </div>
            <div class="erd-list-items" id="erd-list-items">
              <div class="muted" style="padding:10px">Open a session to list tables.</div>
            </div>
          </div>
          <div class="erd-resizer" id="erd-resizer" title="Drag to resize"></div>
          <div class="erd-stage" id="erd-stage" tabindex="0" aria-label="Database diagram canvas">
            <div class="erd-canvas" id="erd-canvas">
              <svg class="erd-links" id="erd-links" xmlns="${SVG_NS}"></svg>
            </div>
            <div class="erd-hint muted" id="erd-hint">
              Double-click a table (or drag it here) to add it.
              Drag a column's ● onto another table's key column to create a relationship.
            </div>
            <div class="erd-status muted" id="erd-status"></div>
          </div>
        </div>
      </div>`;

    this._abort = new AbortController();
    const sig = { signal: this._abort.signal };
    this._model = new DiagramModel();
    this._els = new Map(); // node key -> box element (mirror of model.nodes)
    this._selected = new Set();
    this._selectedEdge = null;
    this._tables = []; // [{schema, name, kind, key, label}]
    this._zoom = 1;
    this._panX = 20;
    this._panY = 20;
    this._stage = this.querySelector("#erd-stage");
    this._canvas = this.querySelector("#erd-canvas");
    this._svg = this.querySelector("#erd-links");
    this._list = this.querySelector("#erd-list-items");
    this._filter = this.querySelector("#erd-filter");

    this._model.onChange(() => {
      this._sync();
      this._saveLayoutSoon();
    });

    this.querySelector(".erd-toolbar").addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act) this._toolbar(act);
    }, sig);
    this._filter.addEventListener("input", () => this._renderList(), sig);
    this._wireList(sig);
    this._wireStage(sig);
    this._wireResizer(sig);
    document.addEventListener("click", () => this._hideMenu(), sig);
    window.addEventListener("resize", () => this._scheduleLinks(), sig);

    try {
      const w = Number(localStorage.getItem(PANEL_KEY));
      if (Number.isFinite(w) && w >= 160) this.querySelector("#erd-list").style.width = `${w}px`;
    } catch {
      /* ignore */
    }

    // Session/database switch → a different diagram; schema change → refresh in place.
    this._ctxKey = null;
    this._unsub = sessionStore.subscribe(() => this._onContextChange());
    this._unsubMeta = bus.on(Events.METADATA_CHANGED, () => this._refresh({ keepLayout: true }));
    this._onContextChange();
  }

  disconnectedCallback() {
    this._saveLayoutNow();
    this._abort?.abort(); // removes every listener registered with the signal
    this._unsub?.();
    this._unsubMeta?.();
    this._hideMenu();
    cancelAnimationFrame(this._linkFrame);
  }

  // --- context / loading ----------------------------------------------------------------

  _ctx() {
    const { sessionId, engine, database, connectionId } = sessionStore.getState();
    return { sessionId, dialect: engine || "postgresql", database, connectionId };
  }

  _layoutKey() {
    const { connectionId, sessionId, database } = this._ctx();
    return `${LAYOUT_PREFIX}${connectionId || sessionId || ""}:${(database || "").toLowerCase()}`;
  }

  _onContextChange() {
    const key = this._ctx().sessionId ? this._layoutKey() : null;
    if (key === this._ctxKey) return;
    this._saveLayoutNow();
    this._ctxKey = key;
    this._model.clear();
    this._model.hidden.clear();
    this._selected.clear();
    this._selectedEdge = null;
    if (!key) {
      this._list.innerHTML = `<div class="muted" style="padding:10px">Open a session to list tables.</div>`;
      return;
    }
    this._refresh({ restore: true });
  }

  async _refresh({ restore = false, keepLayout = false } = {}) {
    const { sessionId } = this._ctx();
    if (!sessionId) return;
    const token = (this._loadToken = (this._loadToken || 0) + 1);
    this._setStatus("Loading tables and relationships…");
    try {
      // One cached round trip for every table (the IntelliSense catalog) + one for every FK,
      // instead of a list-tables call per schema.
      const [tables, rels] = await Promise.all([
        this._listAllTables(sessionId),
        app.api.getRelationships(sessionId).catch(() => ({ relationships: [], junctions: [] })),
      ]);
      if (token !== this._loadToken) return; // a newer load superseded this one
      this._tables = tables
        .map((t) => ({
          schema: t.schema_name || null,
          name: t.name,
          kind: t.kind,
          key: tableKey(t.schema_name, t.name),
          label: t.schema_name ? `${t.schema_name}.${t.name}` : t.name,
        }))
        .sort((a, b) => a.label.localeCompare(b.label));
      this._model.setRelationships(rels);
      this._renderList();
      if (restore) this._restoreLayout();
      if (keepLayout) {
        // Schema changed: re-describe every table on the canvas; drop ones that no longer exist.
        const known = new Set(this._tables.map((t) => t.key));
        for (const node of [...this._model.nodes.values()]) {
          if (!known.has(node.key)) this._model.remove(node.key);
          else this._describe(node.key, node.schema, node.name);
        }
      }
      this._setStatus(
        `${this._tables.length} tables · ${rels.relationships.length} relationships` +
          (rels.junctions.length ? ` · ${rels.junctions.length} many-to-many` : "")
      );
    } catch (err) {
      this._setStatus("");
      bus.emit(Events.TOAST, { message: err?.message || "Could not load the diagram", kind: "error" });
    }
  }

  async _listAllTables(sessionId) {
    try {
      const catalog = await app.api.getCatalog(sessionId);
      return catalog.objects || [];
    } catch {
      // Fallback for servers without the catalog endpoint: per-schema listing.
      const schemas = await app.api.listSchemas(sessionId);
      const lists = await Promise.all(
        schemas.map((s) => app.api.listTables(sessionId, s.name).catch(() => []))
      );
      return lists.flat();
    }
  }

  _setStatus(text) {
    const el = this.querySelector("#erd-status");
    if (el) el.textContent = text;
  }

  // --- adding / removing tables (all through the model) ---------------------------------

  _tableFor(key) {
    return this._tables.find((t) => t.key === key);
  }

  /** Add a table (idempotent). Returns false if it was already on the canvas. */
  addTable(key, at = null) {
    const t = this._tableFor(key);
    if (!t) return false;
    const { added } = this._model.reserve(t.schema, t.name, at); // synchronous reservation
    if (!added) {
      this._focusTable(key); // already there: bring it into view instead of duplicating
      return false;
    }
    this._describe(key, t.schema, t.name);
    return true;
  }

  async _describe(key, schema, name) {
    const { sessionId } = this._ctx();
    try {
      const detail = await app.api.describeTable(sessionId, name, schema || undefined);
      this._model.resolve(key, detail); // no-op if the table was removed meanwhile
    } catch (err) {
      this._model.fail(key);
      bus.emit(Events.TOAST, { message: `Could not load ${name}: ${err?.message || err}`, kind: "error" });
    }
  }

  removeTables(keys) {
    for (const k of keys) {
      this._model.remove(k);
      this._selected.delete(k);
    }
  }

  _addRelated(key) {
    let n = 0;
    for (const k of this._model.relatedKeys(key)) if (this.addTable(k)) n++;
    bus.emit(Events.TOAST, { message: n ? `Added ${n} related table(s)` : "All related tables are already on the diagram", kind: "info" });
  }

  // --- rendering: reconcile DOM ← model ----------------------------------------------------

  _sync() {
    // Remove boxes the model no longer has (this is what makes "remove" always work).
    for (const [key, el] of this._els) {
      if (!this._model.has(key)) {
        el.remove();
        this._els.delete(key);
        this._selected.delete(key);
      }
    }
    // Create / update boxes for every node.
    for (const node of this._model.nodes.values()) {
      let el = this._els.get(node.key);
      if (!el) {
        el = document.createElement("div");
        el.className = "erd-box";
        el.dataset.key = node.key;
        this._canvas.appendChild(el);
        this._els.set(node.key, el);
      }
      if (el.dataset.status !== node.status || el._detail !== node.detail) {
        el.dataset.status = node.status;
        el._detail = node.detail;
        el.innerHTML = this._boxHtml(node);
      }
      el.classList.toggle("selected", this._selected.has(node.key));
      el.classList.toggle("junction", this._model.isJunction(node.key));
      el.style.left = `${node.x}px`;
      el.style.top = `${node.y}px`;
    }
    this.querySelector("#erd-hint")?.classList.toggle("hidden", this._model.nodes.size > 0);
    this._renderListState();
    this._scheduleLinks();
  }

  _boxHtml(node) {
    const title = `${node.schema ? `<span class="erd-schema">${escapeHtml(node.schema)}.</span>` : ""}${escapeHtml(node.name)}`;
    const badge = this._model.isJunction(node.key) ? '<span class="erd-badge" title="Junction table: links two tables many-to-many">M:N</span>' : "";
    const head = `<div class="erd-box-head" title="Drag to move · right-click for options">
        <span class="erd-title">${title}</span>${badge}
        <button class="erd-x" data-remove="${escapeHtml(node.key)}" title="Remove from diagram (Delete)">×</button>
      </div>`;
    if (node.status !== "ready" || !node.detail) {
      return `${head}<div class="erd-box-body"><div class="erd-col erd-loading"><span class="spinner sm"></span> Loading…</div></div>`;
    }
    const fkCols = new Set((node.detail.foreign_keys || []).flatMap((f) => f.columns.map((c) => c.toLowerCase())));
    const uniqueCols = uniqueColumnSet(node.detail);
    const rows = node.detail.columns.map((c) => {
      const lc = c.name.toLowerCase();
      const icons = `${c.primary_key ? '<span class="erd-pk" title="Primary key">🔑</span>' : ""}${
        fkCols.has(lc) ? '<span class="erd-fk" title="Foreign key">FK</span>' : ""}${
        !c.primary_key && uniqueCols.has(lc) ? '<span class="erd-uq" title="Unique">U</span>' : ""}`;
      return `<div class="erd-col" data-col="${escapeHtml(lc)}" title="${escapeHtml(`${c.name} ${displayType(c.data_type)}${c.nullable ? "" : " NOT NULL"}`)}">
          <span class="erd-col-icons">${icons}</span>
          <span class="erd-col-name${c.nullable ? "" : " notnull"}">${escapeHtml(c.name)}</span>
          <span class="erd-col-type">${escapeHtml(displayType(c.data_type))}</span>
          <span class="erd-port" data-port="${escapeHtml(lc)}" title="Drag onto another column to create a relationship"></span>
        </div>`;
    }).join("");
    return `${head}<div class="erd-box-body">${rows}</div>`;
  }

  // --- relationship connectors ---------------------------------------------------------

  _scheduleLinks() {
    cancelAnimationFrame(this._linkFrame);
    this._linkFrame = requestAnimationFrame(() => this._drawLinks());
  }

  _geo(key, columns) {
    const node = this._model.nodes.get(key);
    const el = this._els.get(key);
    if (!node || !el) return null;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let y = node.y + 14;
    const first = columns?.[0];
    if (first) {
      const row = el.querySelector(`.erd-col[data-col="${cssEscape(first.toLowerCase())}"]`);
      const body = el.querySelector(".erd-box-body");
      if (row && body) {
        // Clamp to the visible part of a scrolled column list.
        const rowMid = row.offsetTop + row.offsetHeight / 2 - body.scrollTop;
        y = node.y + Math.max(body.offsetTop + 6, Math.min(rowMid, h - 6));
      }
    }
    return { left: node.x, right: node.x + w, top: node.y, bottom: node.y + h, cx: node.x + w / 2, y };
  }

  _drawLinks() {
    let maxX = 1200;
    let maxY = 900;
    for (const n of this._model.nodes.values()) {
      const el = this._els.get(n.key);
      maxX = Math.max(maxX, n.x + (el?.offsetWidth || 240) + 200);
      maxY = Math.max(maxY, n.y + (el?.offsetHeight || 200) + 200);
    }
    this._svg.setAttribute("width", maxX);
    this._svg.setAttribute("height", maxY);
    const parts = [];
    for (const e of this._model.edges()) {
      const a = this._geo(e.child, e.childColumns);
      const b = this._geo(e.parent, e.parentColumns);
      if (!a || !b) continue;
      const sel = this._selectedEdge === e.id ? " selected" : "";
      const tip = e.kind === "many-to-many"
        ? `many-to-many via ${e.via}`
        : `${e.name || "FK"}: ${e.child}(${e.childColumns.join(", ")}) → ${e.parent}(${e.parentColumns.join(", ")}) · ${e.kind}`;
      let d;
      let ends;
      if (e.self) {
        const x = a.right;
        const loop = 28;
        const y1 = a.y;
        const y2 = b.y === a.y ? a.y + 18 : b.y;
        d = `M ${x} ${y1} H ${x + loop} V ${y2} H ${x}`;
        ends = [[x, y1, 1], [x, y2, 1]];
      } else {
        let ax;
        let bx;
        let ad;
        let bd;
        if (b.cx >= a.cx) {
          if (b.left > a.right + 30) [ax, ad, bx, bd] = [a.right, 1, b.left, -1];
          else [ax, ad, bx, bd] = [a.right, 1, b.right, 1];
        } else if (a.left > b.right + 30) [ax, ad, bx, bd] = [a.left, -1, b.right, 1];
        else [ax, ad, bx, bd] = [a.left, -1, b.left, -1];
        const s1 = ax + ad * 22;
        const s2 = bx + bd * 22;
        const mid = ad !== bd ? (s1 + s2) / 2 : ad > 0 ? Math.max(s1, s2) : Math.min(s1, s2);
        d = `M ${ax} ${a.y} H ${mid} V ${b.y} H ${bx}`;
        ends = [[ax, a.y, ad], [bx, b.y, bd]];
      }
      const childEnd = e.kind === "one-to-one" ? "one" : "many";
      const parentEnd = e.kind === "many-to-many" ? "many" : "one";
      const dash = e.kind === "many-to-many" ? ' stroke-dasharray="6 4"' : "";
      parts.push(`<g class="erd-edge${sel} ${e.kind}" data-edge="${escapeHtml(e.id)}">
          <title>${escapeHtml(tip)}</title>
          <path class="erd-link-hit" d="${d}"></path>
          <path class="erd-link" d="${d}"${dash}></path>
          ${marker(ends[0], childEnd)}${marker(ends[1], parentEnd)}
          ${e.kind === "many-to-many" ? label(ends, `M:N · ${escapeHtml(e.via)}`) : ""}
        </g>`);
    }
    if (this._relateLine) parts.push(this._relateLine);
    this._svg.innerHTML = parts.join("");
  }

  // --- interaction: list ------------------------------------------------------------------

  _renderList() {
    const q = this._filter.value.trim().toLowerCase();
    const items = this._tables.filter((t) => !q || t.label.toLowerCase().includes(q));
    this._list.innerHTML =
      items.map((t) => `<div class="erd-list-item" draggable="true" data-key="${escapeHtml(t.key)}"
          title="Double-click or drag onto the canvas to add">
          <span class="erd-dot"></span><span class="erd-list-label">${escapeHtml(t.label)}</span>
          ${t.kind === "view" ? '<span class="badge">view</span>' : ""}
          <button class="erd-list-toggle" data-toggle="${escapeHtml(t.key)}"></button>
        </div>`).join("") || `<div class="muted" style="padding:10px">No tables.</div>`;
    this._renderListState();
  }

  _renderListState() {
    this._list.querySelectorAll(".erd-list-item").forEach((el) => {
      const on = this._model.has(el.dataset.key);
      el.classList.toggle("on", on);
      const btn = el.querySelector(".erd-list-toggle");
      if (btn) {
        btn.textContent = on ? "✓" : "＋";
        btn.title = on ? "Remove from diagram" : "Add to diagram";
      }
    });
  }

  _wireList(sig) {
    this._list.addEventListener("click", (e) => {
      const toggle = e.target.closest("[data-toggle]");
      if (toggle) {
        e.stopPropagation();
        const key = toggle.dataset.toggle;
        if (this._model.has(key)) this.removeTables([key]);
        else this.addTable(key);
        return;
      }
      const item = e.target.closest(".erd-list-item");
      if (item && this._model.has(item.dataset.key)) this._focusTable(item.dataset.key);
    }, sig);
    // Double-click adds; the model makes a second add a no-op, so it can never duplicate.
    this._list.addEventListener("dblclick", (e) => {
      const item = e.target.closest(".erd-list-item");
      if (item && !e.target.closest("[data-toggle]")) this.addTable(item.dataset.key);
    }, sig);
    this._list.addEventListener("dragstart", (e) => {
      const item = e.target.closest(".erd-list-item");
      if (!item) return;
      e.dataTransfer.setData(DND_TYPE, item.dataset.key);
      e.dataTransfer.setData("text/plain", item.dataset.key);
      e.dataTransfer.effectAllowed = "copy";
    }, sig);
    this._list.addEventListener("contextmenu", (e) => {
      const item = e.target.closest(".erd-list-item");
      if (!item) return;
      e.preventDefault();
      e.stopPropagation();
      const key = item.dataset.key;
      this._showMenu(e.clientX, e.clientY, this._model.has(key)
        ? [{ label: "Remove from diagram", action: () => this.removeTables([key]) },
           { label: "Add related tables", action: () => this._addRelated(key) }]
        : [{ label: "Add to diagram", action: () => this.addTable(key) },
           { label: "Add with related tables", action: () => { this.addTable(key); this._addRelated(key); } }]);
    }, sig);
  }

  // --- interaction: canvas --------------------------------------------------------------

  _toCanvas(clientX, clientY) {
    const r = this._stage.getBoundingClientRect();
    return { x: (clientX - r.left - this._panX) / this._zoom, y: (clientY - r.top - this._panY) / this._zoom };
  }

  _wireStage(sig) {
    const stage = this._stage;
    // Drop a table from the list onto the canvas, at the drop point.
    stage.addEventListener("dragover", (e) => {
      if ([...e.dataTransfer.types].includes(DND_TYPE)) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }
    }, sig);
    stage.addEventListener("drop", (e) => {
      const key = e.dataTransfer.getData(DND_TYPE);
      if (!key) return;
      e.preventDefault();
      const p = this._toCanvas(e.clientX, e.clientY);
      this.addTable(key, { x: Math.max(0, p.x - 40), y: Math.max(0, p.y - 12) });
    }, sig);

    stage.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      stage.focus({ preventScroll: true });
      const removeBtn = e.target.closest("[data-remove]");
      if (removeBtn) return; // handled on click
      const port = e.target.closest(".erd-port");
      if (port) return this._startRelate(e, port);
      const head = e.target.closest(".erd-box-head");
      if (head) return this._startMove(e, head.closest(".erd-box").dataset.key);
      const edge = e.target.closest("[data-edge]");
      if (edge) {
        this._selectEdge(edge.dataset.edge);
        return;
      }
      if (e.target.closest(".erd-box")) {
        this._select(e.target.closest(".erd-box").dataset.key, e.ctrlKey || e.metaKey || e.shiftKey);
        return;
      }
      this._startPan(e);
    }, sig);
    stage.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-remove]");
      if (btn) {
        e.stopPropagation();
        this.removeTables([btn.dataset.remove]);
      }
    }, sig);
    stage.addEventListener("contextmenu", (e) => {
      const edge = e.target.closest("[data-edge]");
      const box = e.target.closest(".erd-box");
      if (!edge && !box) return;
      e.preventDefault();
      e.stopPropagation();
      if (edge) this._edgeMenu(e, edge.dataset.edge);
      else this._boxMenu(e, box.dataset.key);
    }, sig);
    stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = stage.getBoundingClientRect();
      this._zoomAt(this._zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false, signal: sig.signal });
    stage.addEventListener("keydown", (e) => {
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        if (this._selected.size) this.removeTables([...this._selected]);
        else if (this._selectedEdge) this._hideEdge(this._selectedEdge);
      } else if (e.key === "Escape") {
        this._clearSelection();
        this._cancelRelate?.();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
        e.preventDefault();
        this._model.nodes.forEach((n) => this._selected.add(n.key));
        this._sync();
      }
    }, sig);
    // Re-anchor connectors when a box's column list is scrolled.
    this._canvas.addEventListener("scroll", () => this._scheduleLinks(), { capture: true, signal: sig.signal });
  }

  _select(key, additive) {
    if (!additive) this._selected.clear();
    if (additive && this._selected.has(key)) this._selected.delete(key);
    else this._selected.add(key);
    this._selectedEdge = null;
    this._sync();
  }

  _selectEdge(id) {
    this._selected.clear();
    this._selectedEdge = id;
    this._sync();
  }

  _clearSelection() {
    this._selected.clear();
    this._selectedEdge = null;
    this._sync();
  }

  _startMove(e, key) {
    e.preventDefault();
    if (!this._selected.has(key)) this._select(key, e.ctrlKey || e.metaKey || e.shiftKey);
    const group = [...this._selected].map((k) => ({ k, node: this._model.nodes.get(k) })).filter((g) => g.node);
    const start = group.map((g) => ({ k: g.k, x: g.node.x, y: g.node.y }));
    const sx = e.clientX;
    const sy = e.clientY;
    const move = (ev) => {
      const dx = (ev.clientX - sx) / this._zoom;
      const dy = (ev.clientY - sy) / this._zoom;
      for (const s of start) {
        this._model.move(s.k, Math.max(0, s.x + dx), Math.max(0, s.y + dy));
        const el = this._els.get(s.k);
        const n = this._model.nodes.get(s.k);
        if (el && n) {
          el.style.left = `${n.x}px`;
          el.style.top = `${n.y}px`;
        }
      }
      this._scheduleLinks();
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      this._saveLayoutSoon();
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  _startPan(e) {
    this._clearSelection();
    const sx = e.clientX;
    const sy = e.clientY;
    const px = this._panX;
    const py = this._panY;
    this._stage.classList.add("panning");
    const move = (ev) => {
      this._panX = px + (ev.clientX - sx);
      this._panY = py + (ev.clientY - sy);
      this._applyTransform();
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      this._stage.classList.remove("panning");
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  // Drag from a column's port onto another column → propose a FOREIGN KEY.
  _startRelate(e, port) {
    e.preventDefault();
    e.stopPropagation();
    const fromKey = port.closest(".erd-box").dataset.key;
    const fromCol = port.dataset.port;
    const start = this._geo(fromKey, [fromCol]);
    if (!start) return;
    const sx = start.right;
    let target = null;
    const move = (ev) => {
      const p = this._toCanvas(ev.clientX, ev.clientY);
      this._relateLine = `<path class="erd-relate-line" d="M ${sx} ${start.y} L ${p.x} ${p.y}"></path>`;
      target?.classList.remove("relate-target");
      const row = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".erd-col[data-col]");
      target = row && !(row.dataset.col === fromCol && row.closest(".erd-box").dataset.key === fromKey) ? row : null;
      target?.classList.add("relate-target");
      this._scheduleLinks();
    };
    const finish = (commit) => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      this._cancelRelate = null;
      this._relateLine = null;
      target?.classList.remove("relate-target");
      this._scheduleLinks();
      if (commit && target) {
        this._proposeRelationship(
          { key: fromKey, col: fromCol },
          { key: target.closest(".erd-box").dataset.key, col: target.dataset.col }
        );
      }
    };
    const up = () => finish(true);
    this._cancelRelate = () => finish(false);
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    this._stage.classList.add("relating");
    document.addEventListener("mouseup", () => this._stage.classList.remove("relating"), { once: true });
  }

  _end(key, col) {
    const node = this._model.nodes.get(key);
    const c = node?.detail?.columns.find((x) => x.name.toLowerCase() === col);
    if (!node || !c) return null;
    const single = uniqueColumnSet(node.detail, true);
    const pk = node.detail.primary_key || [];
    const isKey = (pk.length === 1 && pk[0].toLowerCase() === col) || single.has(col);
    return { schema: node.schema, table: node.name, column: c.name, type: c.data_type, isKey };
  }

  _proposeRelationship(from, to) {
    const source = this._end(from.key, from.col);
    const target = this._end(to.key, to.col);
    if (!source || !target) return;
    const plan = planRelationship(source, target);
    if (!plan.ok) {
      bus.emit(Events.TOAST, { message: plan.problems[0], kind: "error" });
      return;
    }
    const exists = this._model.relationships.some((r) =>
      tableKey(r.child_schema, r.child_table) === tableKey(plan.child.schema, plan.child.table) &&
      tableKey(r.parent_schema, r.parent_table) === tableKey(plan.parent.schema, plan.parent.table) &&
      r.child_columns.length === 1 && r.child_columns[0].toLowerCase() === plan.child.column.toLowerCase());
    if (exists) {
      bus.emit(Events.TOAST, { message: "That relationship already exists.", kind: "info" });
      return;
    }
    this._relationshipDialog(plan);
  }

  _relationshipDialog(plan) {
    const { dialect } = this._ctx();
    const form = document.createElement("form");
    form.className = "modal-form";
    const actions = ["NO ACTION", "CASCADE", "SET NULL", ...(dialect === "mysql" ? ["RESTRICT"] : ["SET DEFAULT"])];
    const opts = actions.map((a) => `<option>${a}</option>`).join("");
    form.innerHTML = `
      <p class="muted" style="margin:0 0 8px">
        <strong>${escapeHtml(plan.child.table)}.${escapeHtml(plan.child.column)}</strong> (foreign key) →
        <strong>${escapeHtml(plan.parent.table)}.${escapeHtml(plan.parent.column)}</strong> (referenced key)
      </p>
      ${plan.warnings.map((w) => `<div class="erd-warn">⚠ ${escapeHtml(w)}</div>`).join("")}
      <div class="field"><label>Constraint name</label>
        <input class="input" name="name" required maxlength="120" value="${escapeHtml(defaultConstraintName(plan.child, plan.parent))}"></div>
      <div class="row">
        <div class="field" style="flex:1"><label>On delete</label><select class="input" name="onDelete">${opts}</select></div>
        <div class="field" style="flex:1"><label>On update</label><select class="input" name="onUpdate">${opts}</select></div>
      </div>
      <div class="field"><label>SQL that will run (permission-checked)</label>
        <pre class="erd-sql" id="erd-sql"></pre></div>
      <div class="row" style="justify-content:flex-end; gap:8px">
        <button type="button" class="btn btn-ghost" data-open-editor>Open in SQL Editor</button>
        <button class="btn btn-primary" type="submit">Create relationship</button>
      </div>`;
    const sqlBox = form.querySelector("#erd-sql");
    const build = () => {
      const d = Object.fromEntries(new FormData(form).entries());
      return addForeignKeySql(dialect, { child: plan.child, parent: plan.parent, name: d.name.trim(), onDelete: d.onDelete, onUpdate: d.onUpdate });
    };
    const render = () => {
      const sql = build();
      sqlBox.textContent = sql ?? "-- SQLite cannot add a FOREIGN KEY to an existing table.\n-- Recreate the table with the constraint (open its definition in the SQL Editor).";
      form.querySelector("[type=submit]").disabled = !sql;
    };
    form.addEventListener("input", render);
    render();
    const close = openModal({ title: "Create relationship", content: form, width: 560 });
    form.querySelector("[data-open-editor]").addEventListener("click", async () => {
      const sql = build() ?? (await this._tableDefinition(plan.child));
      setPendingEditorSql(sql || "");
      close();
      app.router.navigate("/editor");
    });
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const sql = build();
      if (!sql) return;
      if (await this._executeDdl(sql, "Relationship created")) close();
    });
  }

  async _tableDefinition(end) {
    try {
      const { sessionId } = this._ctx();
      const res = await app.api.getObjectDefinition(sessionId, { objectType: "table", name: end.table, schema: end.schema });
      return res?.definition || null;
    } catch {
      return null;
    }
  }

  /** Run DDL through the normal, permission-checked script endpoint; refresh on success. */
  async _executeDdl(sql, successMessage) {
    const { sessionId } = this._ctx();
    try {
      const res = await app.api.executeScript(sessionId, sql);
      const failed = (res.statements || []).find((s) => !s.success);
      if (failed) {
        bus.emit(Events.TOAST, { message: failed.error || failed.error_code || "Failed", kind: "error" });
        return false;
      }
      bus.emit(Events.TOAST, { message: successMessage, kind: "success" });
      clearMetadataCache();
      bus.emit(Events.METADATA_CHANGED, { sessionId }); // refreshes this diagram + other views
      return true;
    } catch (err) {
      bus.emit(Events.TOAST, { message: err?.message || "Failed", kind: "error" });
      return false;
    }
  }

  _hideEdge(id) {
    this._model.hideEdge(id);
    this._selectedEdge = null;
    bus.emit(Events.TOAST, { message: "Connector hidden from the diagram (the database is unchanged)", kind: "info" });
  }

  _edgeMenu(e, id) {
    this._selectEdge(id);
    const edge = this._model.edges().find((x) => x.id === id);
    if (!edge) return;
    const items = [{ label: "Hide connector (diagram only)", action: () => this._hideEdge(id) }];
    if (edge.kind === "many-to-many") {
      const j = this._tables.find((t) => t.name === edge.via);
      if (j) items.push({ label: `Add junction table "${edge.via}"`, action: () => this.addTable(j.key) });
    } else {
      const { dialect } = this._ctx();
      const sql = dropForeignKeySql(dialect, {
        childSchema: edge.relationship.child_schema, childTable: edge.relationship.child_table, name: edge.name,
      });
      if (sql) {
        items.push({
          label: "Delete relationship from database…",
          action: async () => {
            const ok = await confirm({
              title: `Drop foreign key "${edge.name}"?`,
              code: sql,
              text: "This changes the database schema and can't be undone from here.",
              confirmText: "Drop constraint",
              danger: true,
            });
            if (ok) await this._executeDdl(sql, `Relationship "${edge.name}" dropped`);
          },
        });
      }
    }
    if (this._model.hidden.size) items.push({ label: `Show hidden connectors (${this._model.hidden.size})`, action: () => this._model.showAllEdges() });
    this._showMenu(e.clientX, e.clientY, items);
  }

  _boxMenu(e, key) {
    if (!this._selected.has(key)) this._select(key, false);
    const node = this._model.nodes.get(key);
    const items = [
      { label: "Remove from diagram", action: () => this.removeTables([key]) },
      { label: "Add related tables", action: () => this._addRelated(key) },
      {
        label: "Edit table (ALTER script)…",
        action: async () => {
          const sql = await this._tableDefinition({ schema: node?.schema, table: node?.name });
          if (!sql) return bus.emit(Events.TOAST, { message: "Definition not available", kind: "error" });
          setPendingEditorSql(sql);
          app.router.navigate("/editor");
        },
      },
    ];
    if (this._selected.size > 1) {
      items.push({ label: `Remove selected tables (${this._selected.size})`, action: () => this.removeTables([...this._selected]) });
    }
    this._showMenu(e.clientX, e.clientY, items);
  }

  _showMenu(x, y, items) {
    this._hideMenu();
    const menu = document.createElement("div");
    menu.className = "erd-menu";
    for (const it of items) {
      const b = document.createElement("button");
      b.className = "erd-menu-item";
      b.textContent = it.label;
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._hideMenu();
        it.action();
      });
      menu.appendChild(b);
    }
    menu.style.left = `${Math.min(x, window.innerWidth - 240)}px`;
    menu.style.top = `${Math.min(y, window.innerHeight - 40 * items.length - 10)}px`;
    document.body.appendChild(menu);
    this._menu = menu;
  }

  _hideMenu() {
    this._menu?.remove();
    this._menu = null;
  }

  // --- toolbar / view ------------------------------------------------------------------

  _toolbar(act) {
    if (act === "refresh") {
      clearMetadataCache();
      return this._refresh({ keepLayout: true });
    }
    if (act === "layout") return this._autoLayout();
    if (act === "fit") return this._fit();
    if (act === "zoom-in") return this._zoomAt(this._zoom * 1.2);
    if (act === "zoom-out") return this._zoomAt(this._zoom / 1.2);
    if (act === "zoom-reset") return this._zoomAt(1);
    if (act === "clear") {
      this._model.clear();
      this._selected.clear();
    }
  }

  _autoLayout() {
    const sizes = new Map();
    for (const [k, el] of this._els) sizes.set(k, { w: el.offsetWidth, h: el.offsetHeight });
    this._model.autoLayout(sizes);
    this._fit();
  }

  _fit() {
    if (!this._model.nodes.size) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const n of this._model.nodes.values()) {
      const el = this._els.get(n.key);
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + (el?.offsetWidth || 240));
      maxY = Math.max(maxY, n.y + (el?.offsetHeight || 200));
    }
    const r = this._stage.getBoundingClientRect();
    const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.min((r.width - 60) / (maxX - minX), (r.height - 60) / (maxY - minY), 1.2)));
    this._zoom = z;
    this._panX = (r.width - (maxX - minX) * z) / 2 - minX * z;
    this._panY = (r.height - (maxY - minY) * z) / 2 - minY * z;
    this._applyTransform();
  }

  _focusTable(key) {
    const n = this._model.nodes.get(key);
    const el = this._els.get(key);
    if (!n || !el) return;
    const r = this._stage.getBoundingClientRect();
    this._panX = r.width / 2 - (n.x + el.offsetWidth / 2) * this._zoom;
    this._panY = r.height / 3 - n.y * this._zoom;
    this._applyTransform();
    this._select(key, false);
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 700);
  }

  _zoomAt(z, cx = null, cy = null) {
    const r = this._stage.getBoundingClientRect();
    const ox = cx ?? r.width / 2;
    const oy = cy ?? r.height / 2;
    const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
    // Keep the point under the cursor fixed while zooming.
    this._panX = ox - (ox - this._panX) * (next / this._zoom);
    this._panY = oy - (oy - this._panY) * (next / this._zoom);
    this._zoom = next;
    this._applyTransform();
  }

  _applyTransform() {
    this._canvas.style.transform = `translate(${this._panX}px, ${this._panY}px) scale(${this._zoom})`;
    const label = this.querySelector(".erd-zoom-label");
    if (label) label.textContent = `${Math.round(this._zoom * 100)}%`;
  }

  // --- persistence (per connection + database, this browser) ---------------------------

  _saveLayoutSoon() {
    clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(() => this._saveLayoutNow(), 400);
  }

  _saveLayoutNow() {
    clearTimeout(this._saveTimer);
    if (!this._ctxKey) return;
    try {
      const data = this._model.serialize();
      if (data.nodes.length) localStorage.setItem(this._ctxKey, JSON.stringify(data));
      else localStorage.removeItem(this._ctxKey);
    } catch {
      /* storage unavailable */
    }
  }

  _restoreLayout() {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(this._ctxKey) || "null");
    } catch {
      saved = null;
    }
    if (!saved?.nodes?.length) return;
    (saved.hidden || []).forEach((id) => this._model.hidden.add(id));
    for (const n of saved.nodes) {
      const key = tableKey(n.schema, n.name);
      if (this._tableFor(key)) this.addTable(key, { x: n.x, y: n.y });
    }
    requestAnimationFrame(() => this._fit());
  }

  // --- resizable list / canvas divider ------------------------------------------------

  _wireResizer(sig) {
    const resizer = this.querySelector("#erd-resizer");
    const panel = this.querySelector("#erd-list");
    const main = this.querySelector(".erd-main");
    resizer.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = panel.getBoundingClientRect().width;
      const move = (ev) => {
        const max = main.getBoundingClientRect().width - 240;
        panel.style.width = `${Math.max(160, Math.min(startW + (ev.clientX - startX), max))}px`;
      };
      const stop = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", stop);
        document.body.style.userSelect = "";
        try {
          localStorage.setItem(PANEL_KEY, String(Math.round(panel.getBoundingClientRect().width)));
        } catch {
          /* ignore */
        }
      };
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", stop);
    }, sig);
  }
}

// --- helpers ----------------------------------------------------------------------------------

/** Lower-cased columns covered by a unique index/constraint (single-column only if `single`). */
function uniqueColumnSet(detail, single = false) {
  const out = new Set();
  for (const ix of detail?.indexes || []) {
    if (!ix.unique || ix.primary) continue;
    if (single && ix.columns.length !== 1) continue;
    ix.columns.forEach((c) => out.add(String(c).toLowerCase()));
  }
  return out;
}

function cssEscape(s) {
  return globalThis.CSS?.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&");
}

/** Crow's-foot end marker at (x, y); `dir` points away from the box (+1 right, −1 left). */
function marker([x, y, dir], kind) {
  const bar = (o) => `M ${x + dir * o} ${y - 6} V ${y + 6}`;
  if (kind === "many") {
    const tip = x + dir * 14;
    return `<path class="erd-mark" d="M ${tip} ${y} L ${x} ${y - 7} M ${tip} ${y} L ${x} ${y} M ${tip} ${y} L ${x} ${y + 7} ${bar(18)}"></path>`;
  }
  return `<path class="erd-mark" d="${bar(9)} ${bar(14)}"></path>`; // exactly one: ||
}

function label(ends, text) {
  const [[x1, y1], [x2, y2]] = ends;
  return `<text class="erd-edge-label" x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 6}" text-anchor="middle">${text}</text>`;
}

customElements.define("diagram-view", DiagramView);
