// Database Diagram (ER diagram) — SSMS-style. A resizable left panel lists the database's
// tables; clicking (or the right-click menu) adds/removes them from the diagram workspace on
// the right. Each table box shows columns with primary-key (🔑) / foreign-key (FK) markers and
// types; relationship lines come from foreign-key metadata. Supports zoom, pan, dragging and
// selecting boxes, and a right-click context menu (add / remove / remove selected). Removing a
// table affects only the diagram, never the database.
//
// Metadata comes from the same per-session endpoints the Schema Explorer uses, so it works for
// SQL Server, MySQL and PostgreSQL alike.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore } from "../../core/session-state.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";

const BOX_W = 210;
const GAP = 40;
const PANEL_KEY = "dbadmin.erd-panel-width";

export class DiagramView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="erd-layout">
        <div class="editor-toolbar erd-toolbar">
          <session-bar></session-bar>
          <span class="spacer"></span>
          <button class="btn btn-ghost" id="erd-zoom-out" title="Zoom out">−</button>
          <button class="btn btn-ghost" id="erd-zoom-reset" title="Reset view">100%</button>
          <button class="btn btn-ghost" id="erd-zoom-in" title="Zoom in">+</button>
          <button class="btn btn-ghost btn-danger" id="erd-clear" title="Clear diagram">Clear</button>
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
          <div class="erd-stage" id="erd-stage">
            <div class="erd-canvas" id="erd-canvas"><svg class="erd-links" id="erd-links"></svg></div>
            <div class="erd-hint muted" id="erd-hint">Click a table on the left to add it. Right-click a table box for options.</div>
          </div>
        </div>
      </div>`;

    this._boxes = new Map(); // key "schema.name" -> { detail, x, y, el }
    this._selected = new Set(); // selected box keys
    this._allTables = []; // [{key,label}]
    this._zoom = 1;
    this._panX = 20;
    this._panY = 20;
    this._stage = this.querySelector("#erd-stage");
    this._canvas = this.querySelector("#erd-canvas");
    this._svg = this.querySelector("#erd-links");
    this._list = this.querySelector("#erd-list-items");

    this.querySelector("#erd-clear").addEventListener("click", () => this._clear());
    this.querySelector("#erd-zoom-in").addEventListener("click", () => this._setZoom(this._zoom * 1.2));
    this.querySelector("#erd-zoom-out").addEventListener("click", () => this._setZoom(this._zoom / 1.2));
    this.querySelector("#erd-zoom-reset").addEventListener("click", () => {
      this._zoom = 1;
      this._panX = 20;
      this._panY = 20;
      this._applyTransform();
      this._setZoom(1);
    });
    this.querySelector("#erd-filter").addEventListener("input", (e) => this._renderList(e.target.value));
    this._wireStage();
    this._wireResizer();

    // Restore the saved panel width.
    try {
      const w = Number(localStorage.getItem(PANEL_KEY));
      if (Number.isFinite(w) && w >= 160) this.querySelector("#erd-list").style.width = `${w}px`;
    } catch {
      /* ignore */
    }

    this._unsub = sessionStore.subscribe(() => this._loadTables());
    this._closeMenu = () => this._hideMenu();
    document.addEventListener("click", this._closeMenu);
    this._loadTables();
  }

  disconnectedCallback() {
    this._unsub?.();
    document.removeEventListener("click", this._closeMenu);
    this._hideMenu();
  }

  _sid() {
    return sessionStore.getState().sessionId;
  }

  async _loadTables() {
    const sid = this._sid();
    if (!sid) return;
    try {
      const schemas = await app.api.listSchemas(sid);
      const lists = await Promise.all(
        schemas.map((s) => app.api.listTables(sid, s.name).catch(() => []))
      );
      this._allTables = lists
        .flat()
        .map((t) => ({
          key: `${t.schema_name || ""}.${t.name}`,
          label: t.schema_name ? `${t.schema_name}.${t.name}` : t.name,
        }))
        .sort((a, b) => a.label.localeCompare(b.label));
      this._renderList(this.querySelector("#erd-filter").value);
    } catch (err) {
      bus.emit(Events.TOAST, { message: err?.message || "Could not list tables", kind: "error" });
    }
  }

  _renderList(filter = "") {
    const q = filter.trim().toLowerCase();
    const items = this._allTables.filter((t) => !q || t.label.toLowerCase().includes(q));
    this._list.innerHTML =
      items
        .map(
          (t) =>
            `<div class="erd-list-item${this._boxes.has(t.key) ? " on" : ""}" data-key="${escapeHtml(t.key)}">
              <span class="erd-dot"></span>${escapeHtml(t.label)}</div>`
        )
        .join("") || `<div class="muted" style="padding:10px">No tables.</div>`;
    this._list.querySelectorAll(".erd-list-item").forEach((el) => {
      el.addEventListener("click", () => this._toggleTable(el.dataset.key));
      el.addEventListener("contextmenu", (e) => this._listMenu(e, el.dataset.key));
    });
  }

  async _toggleTable(key) {
    if (this._boxes.has(key)) {
      this._removeBox(key);
    } else {
      await this._addTable(key);
    }
  }

  async _addTable(key) {
    const sid = this._sid();
    if (!sid) {
      bus.emit(Events.TOAST, { message: "Open a session first", kind: "error" });
      return;
    }
    if (this._boxes.has(key)) return;
    const [schema, name] = splitKey(key);
    try {
      const detail = await app.api.describeTable(sid, name, schema || undefined);
      this.querySelector("#erd-hint")?.remove();
      this._addBox(key, detail);
      this._drawLinks();
      this._renderList(this.querySelector("#erd-filter").value);
    } catch (err) {
      bus.emit(Events.TOAST, { message: `Could not load ${name}: ${err.message}`, kind: "error" });
    }
  }

  _addBox(key, detail) {
    const idx = this._boxes.size;
    const el = document.createElement("div");
    el.className = "erd-box";
    el.dataset.key = key;
    const cols = detail.columns
      .map((c) => {
        const pk = c.primary_key ? '<span class="erd-pk" title="Primary key">🔑</span>' : "";
        const fk = this._isFk(detail, c.name) ? '<span class="erd-fk" title="Foreign key">FK</span>' : "";
        return `<div class="erd-col"><span class="erd-col-name">${pk}${fk}${escapeHtml(c.name)}</span>
          <span class="erd-col-type">${escapeHtml(shortType(c.data_type))}</span></div>`;
      })
      .join("");
    el.innerHTML = `
      <div class="erd-box-head" title="Drag to move · right-click for options">${escapeHtml(detail.table.name)}</div>
      <div class="erd-box-body">${cols}</div>`;
    const x = GAP + (idx % 4) * (BOX_W + GAP);
    const y = GAP + Math.floor(idx / 4) * 220;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    this._canvas.appendChild(el);
    const box = { detail, key, x, y, el };
    this._boxes.set(key, box);
    this._makeDraggable(box);
    el.addEventListener("contextmenu", (e) => this._boxMenu(e, key));
  }

  _removeBox(key) {
    const box = this._boxes.get(key);
    if (!box) return;
    box.el.remove();
    this._boxes.delete(key);
    this._selected.delete(key);
    this._drawLinks();
    this._renderList(this.querySelector("#erd-filter").value);
    if (!this._boxes.size && !this.querySelector("#erd-hint")) {
      const hint = document.createElement("div");
      hint.className = "erd-hint muted";
      hint.id = "erd-hint";
      hint.textContent = "Click a table on the left to add it. Right-click a table box for options.";
      this._stage.appendChild(hint);
    }
  }

  _isFk(detail, colName) {
    return (detail.foreign_keys || []).some((fk) => fk.columns.includes(colName));
  }

  _makeDraggable(box) {
    const head = box.el.querySelector(".erd-box-head");
    head.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return; // ignore right-click
      e.preventDefault();
      e.stopPropagation();
      this._selectBox(box.key, e.ctrlKey || e.metaKey);
      const startX = e.clientX;
      const startY = e.clientY;
      const ox = box.x;
      const oy = box.y;
      const move = (ev) => {
        box.x = ox + (ev.clientX - startX) / this._zoom;
        box.y = oy + (ev.clientY - startY) / this._zoom;
        box.el.style.left = `${box.x}px`;
        box.el.style.top = `${box.y}px`;
        this._drawLinks();
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }

  _selectBox(key, additive) {
    if (!additive) {
      this._selected.clear();
      this._boxes.forEach((b) => b.el.classList.remove("selected"));
    }
    this._selected.add(key);
    this._boxes.get(key)?.el.classList.add("selected");
  }

  // --- context menus -------------------------------------------------------------------

  _boxMenu(e, key) {
    e.preventDefault();
    e.stopPropagation();
    if (!this._selected.has(key)) this._selectBox(key, false);
    const items = [
      { label: "Remove table from diagram", action: () => this._removeBox(key) },
    ];
    if (this._selected.size > 1) {
      items.push({
        label: `Remove selected tables (${this._selected.size})`,
        action: () => [...this._selected].forEach((k) => this._removeBox(k)),
      });
    }
    this._showMenu(e.clientX, e.clientY, items);
  }

  _listMenu(e, key) {
    e.preventDefault();
    e.stopPropagation();
    const onDiagram = this._boxes.has(key);
    this._showMenu(e.clientX, e.clientY, [
      onDiagram
        ? { label: "Remove table from diagram", action: () => this._removeBox(key) }
        : { label: "Add table to diagram", action: () => this._addTable(key) },
    ]);
  }

  _showMenu(x, y, items) {
    this._hideMenu();
    const menu = document.createElement("div");
    menu.className = "erd-menu";
    items.forEach((it) => {
      const b = document.createElement("button");
      b.className = "erd-menu-item";
      b.textContent = it.label;
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._hideMenu();
        it.action();
      });
      menu.appendChild(b);
    });
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    document.body.appendChild(menu);
    this._menu = menu;
  }

  _hideMenu() {
    this._menu?.remove();
    this._menu = null;
  }

  // --- zoom / pan ----------------------------------------------------------------------

  _wireStage() {
    this._stage.addEventListener("mousedown", (e) => {
      if (e.target.closest(".erd-box") || e.button !== 0) return;
      this._selectBox(null, false); // click empty space clears selection
      this._selected.clear();
      this._boxes.forEach((b) => b.el.classList.remove("selected"));
      const sx = e.clientX;
      const sy = e.clientY;
      const px = this._panX;
      const py = this._panY;
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
      this._stage.classList.add("panning");
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    this._stage.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        this._setZoom(this._zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
      },
      { passive: false }
    );
  }

  _setZoom(z) {
    this._zoom = Math.min(2.5, Math.max(0.3, z));
    this._applyTransform();
    const label = this.querySelector("#erd-zoom-reset");
    if (label) label.textContent = `${Math.round(this._zoom * 100)}%`;
  }

  _applyTransform() {
    this._canvas.style.transform = `translate(${this._panX}px, ${this._panY}px) scale(${this._zoom})`;
  }

  // --- resizable list/diagram divider --------------------------------------------------

  _wireResizer() {
    const resizer = this.querySelector("#erd-resizer");
    const panel = this.querySelector("#erd-list");
    const main = this.querySelector(".erd-main");
    const drag = (clientX) => {
      const startX = clientX;
      const startW = panel.getBoundingClientRect().width;
      const move = (x) => {
        const max = main.getBoundingClientRect().width - 240;
        const w = Math.max(160, Math.min(startW + (x - startX), max));
        panel.style.width = `${w}px`;
      };
      const onMouseMove = (e) => move(e.clientX);
      const onTouchMove = (e) => e.touches[0] && move(e.touches[0].clientX);
      const stop = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", stop);
        document.removeEventListener("touchmove", onTouchMove);
        document.removeEventListener("touchend", stop);
        document.body.style.userSelect = "";
        try {
          localStorage.setItem(PANEL_KEY, String(Math.round(panel.getBoundingClientRect().width)));
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
    resizer.addEventListener("mousedown", (e) => {
      e.preventDefault();
      drag(e.clientX);
    });
    resizer.addEventListener("touchstart", (e) => e.touches[0] && drag(e.touches[0].clientX), {
      passive: true,
    });
  }

  // --- relationship lines --------------------------------------------------------------

  _drawLinks() {
    const ns = "http://www.w3.org/2000/svg";
    let maxX = 1000;
    let maxY = 800;
    for (const b of this._boxes.values()) {
      maxX = Math.max(maxX, b.x + b.el.offsetWidth + 100);
      maxY = Math.max(maxY, b.y + b.el.offsetHeight + 100);
    }
    this._svg.setAttribute("width", maxX);
    this._svg.setAttribute("height", maxY);
    this._svg.innerHTML = "";
    for (const child of this._boxes.values()) {
      for (const fk of child.detail.foreign_keys || []) {
        const parent = this._findBox(fk.referred_table);
        if (!parent) continue;
        const a = center(child);
        const b = center(parent);
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", a.x);
        line.setAttribute("y1", a.y);
        line.setAttribute("x2", b.x);
        line.setAttribute("y2", b.y);
        line.setAttribute("class", "erd-link");
        this._svg.appendChild(line);
        this._svg.appendChild(dot(ns, a.x, a.y, "erd-many")); // FK (many) end
        this._svg.appendChild(dot(ns, b.x, b.y, "erd-one")); // PK (one) end
      }
    }
  }

  _findBox(table) {
    for (const b of this._boxes.values()) {
      if (b.detail.table.name.toLowerCase() === String(table).toLowerCase()) return b;
    }
    return null;
  }

  _clear() {
    [...this._boxes.keys()].forEach((k) => this._removeBox(k));
  }
}

function splitKey(key) {
  const i = key.indexOf(".");
  return i === -1 ? ["", key] : [key.slice(0, i), key.slice(i + 1)];
}

function center(box) {
  return { x: box.x + box.el.offsetWidth / 2, y: box.y + box.el.offsetHeight / 2 };
}

function dot(ns, x, y, cls) {
  const c = document.createElementNS(ns, "circle");
  c.setAttribute("cx", x);
  c.setAttribute("cy", y);
  c.setAttribute("r", 4);
  c.setAttribute("class", cls);
  return c;
}

function shortType(t) {
  return String(t || "").replace(/\s+/g, " ").slice(0, 22);
}

customElements.define("diagram-view", DiagramView);
