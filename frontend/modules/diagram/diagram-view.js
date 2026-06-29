// Database Diagram (ER diagram) — SSMS-style. Pick a session/database, choose one or more
// tables, and render an entity-relationship diagram: each table box lists its columns with
// primary-key (🔑) and foreign-key (FK) markers and data types; relationship lines are drawn
// from foreign-key metadata. Supports zoom, pan, dragging table boxes, and adding more tables.
//
// Metadata comes from the same per-session endpoints the Schema Explorer uses (list schemas /
// tables / describe table), so it works for SQL Server, MySQL and PostgreSQL alike.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore } from "../../core/session-state.js";
import { loadTomSelect } from "../../core/tom-select.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";

const BOX_W = 210;
const GAP = 40;

export class DiagramView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="erd-layout">
        <div class="editor-toolbar erd-toolbar">
          <session-bar></session-bar>
          <span class="sep"></span>
          <select class="input erd-tables" multiple></select>
          <button class="btn btn-primary" id="erd-add">Add to diagram</button>
          <span class="spacer"></span>
          <button class="btn btn-ghost" id="erd-zoom-out" title="Zoom out">−</button>
          <button class="btn btn-ghost" id="erd-zoom-reset" title="Reset view">100%</button>
          <button class="btn btn-ghost" id="erd-zoom-in" title="Zoom in">+</button>
          <button class="btn btn-ghost btn-danger" id="erd-clear" title="Clear diagram">Clear</button>
        </div>
        <div class="erd-stage" id="erd-stage">
          <div class="erd-canvas" id="erd-canvas">
            <svg class="erd-links" id="erd-links"></svg>
          </div>
          <div class="erd-hint muted" id="erd-hint">Select a session and one or more tables, then “Add to diagram”.</div>
        </div>
      </div>`;

    this._boxes = new Map(); // "schema.name" -> { detail, x, y, el }
    this._zoom = 1;
    this._panX = 20;
    this._panY = 20;
    this._stage = this.querySelector("#erd-stage");
    this._canvas = this.querySelector("#erd-canvas");
    this._svg = this.querySelector("#erd-links");
    this._picker = this.querySelector(".erd-tables");

    this.querySelector("#erd-add").addEventListener("click", () => this._addSelected());
    this.querySelector("#erd-clear").addEventListener("click", () => this._clear());
    this.querySelector("#erd-zoom-in").addEventListener("click", () => this._setZoom(this._zoom * 1.2));
    this.querySelector("#erd-zoom-out").addEventListener("click", () => this._setZoom(this._zoom / 1.2));
    this.querySelector("#erd-zoom-reset").addEventListener("click", () => {
      this._zoom = 1;
      this._panX = 20;
      this._panY = 20;
      this._applyTransform();
    });
    this._wireStage();

    this._unsub = sessionStore.subscribe(() => this._loadTables());
    loadTomSelect().finally(() => this._loadTables());
  }

  disconnectedCallback() {
    this._unsub?.();
  }

  _sid() {
    return sessionStore.getState().sessionId;
  }

  // Populate the table picker from the active session's schemas/tables.
  async _loadTables() {
    const sid = this._sid();
    if (!sid) return;
    try {
      const schemas = await app.api.listSchemas(sid);
      const lists = await Promise.all(
        schemas.map((s) => app.api.listTables(sid, s.name).catch(() => []))
      );
      const options = [];
      lists.flat().forEach((t) => {
        const key = `${t.schema_name || ""}.${t.name}`;
        options.push({ key, label: t.schema_name ? `${t.schema_name}.${t.name}` : t.name });
      });
      options.sort((a, b) => a.label.localeCompare(b.label));
      this._picker.tomselect?.destroy();
      this._picker.innerHTML = options
        .map((o) => `<option value="${escapeHtml(o.key)}">${escapeHtml(o.label)}</option>`)
        .join("");
      if (window.TomSelect) {
        // eslint-disable-next-line no-new
        new window.TomSelect(this._picker, {
          plugins: ["remove_button"],
          maxItems: null,
          maxOptions: 1000,
          placeholder: "Pick tables…",
        });
      }
    } catch (err) {
      bus.emit(Events.TOAST, { message: err?.message || "Could not list tables", kind: "error" });
    }
  }

  _selectedKeys() {
    return this._picker.tomselect
      ? [...this._picker.tomselect.getValue()].filter(Boolean)
      : [...this._picker.selectedOptions].map((o) => o.value);
  }

  async _addSelected() {
    const sid = this._sid();
    if (!sid) {
      bus.emit(Events.TOAST, { message: "Open a session first", kind: "error" });
      return;
    }
    const keys = this._selectedKeys().filter((k) => !this._boxes.has(k));
    if (!keys.length) return;
    this.querySelector("#erd-hint")?.remove();
    for (const key of keys) {
      const [schema, name] = splitKey(key);
      try {
        const detail = await app.api.describeTable(sid, name, schema || undefined);
        this._addBox(key, detail);
      } catch (err) {
        bus.emit(Events.TOAST, { message: `Could not load ${name}: ${err.message}`, kind: "error" });
      }
    }
    this._autoLayout();
    this._drawLinks();
  }

  _addBox(key, detail) {
    const idx = this._boxes.size;
    const el = document.createElement("div");
    el.className = "erd-box";
    const cols = detail.columns
      .map((c) => {
        const pk = c.primary_key ? '<span class="erd-pk" title="Primary key">🔑</span>' : "";
        const fk = this._isFk(detail, c.name) ? '<span class="erd-fk" title="Foreign key">FK</span>' : "";
        return `<div class="erd-col"><span class="erd-col-name">${pk}${fk}${escapeHtml(c.name)}</span>
          <span class="erd-col-type">${escapeHtml(shortType(c.data_type))}</span></div>`;
      })
      .join("");
    el.innerHTML = `
      <div class="erd-box-head" title="Drag to move">${escapeHtml(detail.table.name)}</div>
      <div class="erd-box-body">${cols}</div>`;
    const x = GAP + (idx % 4) * (BOX_W + GAP);
    const y = GAP + Math.floor(idx / 4) * 220;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    this._canvas.appendChild(el);
    const box = { detail, key, x, y, el };
    this._boxes.set(key, box);
    this._makeDraggable(box);
  }

  _isFk(detail, colName) {
    return (detail.foreign_keys || []).some((fk) => fk.columns.includes(colName));
  }

  _autoLayout() {
    // Only (re)position boxes that are still at their initial spot is overkill; keep current
    // positions — initial placement in _addBox already grids them out.
  }

  _makeDraggable(box) {
    const head = box.el.querySelector(".erd-box-head");
    head.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
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

  // Pan by dragging empty canvas; zoom with the wheel.
  _wireStage() {
    this._stage.addEventListener("mousedown", (e) => {
      if (e.target.closest(".erd-box")) return; // box drag handles itself
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

  // Draw a relationship line for every foreign key whose referenced table is also on the canvas.
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
        const parent = this._findBox(fk.referred_schema, fk.referred_table);
        if (!parent) continue;
        const a = center(child);
        const b = center(parent);
        const path = document.createElementNS(ns, "line");
        path.setAttribute("x1", a.x);
        path.setAttribute("y1", a.y);
        path.setAttribute("x2", b.x);
        path.setAttribute("y2", b.y);
        path.setAttribute("class", "erd-link");
        this._svg.appendChild(path);
        // Crow's-foot-ish markers: dot at the "many" (FK/child) end, bar at the "one" (parent).
        this._svg.appendChild(dot(ns, a.x, a.y, "erd-many"));
        this._svg.appendChild(dot(ns, b.x, b.y, "erd-one"));
      }
    }
  }

  _findBox(schema, table) {
    for (const b of this._boxes.values()) {
      const t = b.detail.table;
      if (t.name.toLowerCase() === String(table).toLowerCase()) return b;
    }
    return null;
  }

  _clear() {
    this._boxes.forEach((b) => b.el.remove());
    this._boxes.clear();
    this._svg.innerHTML = "";
    if (!this.querySelector("#erd-hint")) {
      const hint = document.createElement("div");
      hint.className = "erd-hint muted";
      hint.id = "erd-hint";
      hint.textContent = "Select a session and one or more tables, then “Add to diagram”.";
      this._stage.appendChild(hint);
    }
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
