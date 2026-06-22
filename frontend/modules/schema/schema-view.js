// Schema Explorer: a lazy-loading tree of schemas → tables → columns/indexes, plus routines.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { sessionStore } from "../../core/session-state.js";
import { escapeHtml } from "../../components/view-helpers.js";
import "../../components/session-bar.js";

export class SchemaView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="schema-layout">
        <div class="editor-toolbar"><session-bar></session-bar></div>
        <div class="tree" id="tree"><div class="muted" style="padding:16px">
          Select or open a session to browse its schema.</div></div>
      </div>`;
    this._tree = this.querySelector("#tree");
    this._unsub = sessionStore.subscribe(() => this._loadRoot());
    if (sessionStore.getState().sessionId) this._loadRoot();
  }

  disconnectedCallback() {
    this._unsub?.();
  }

  _session() {
    return sessionStore.getState().sessionId;
  }

  async _loadRoot() {
    const sid = this._session();
    if (!sid) return;
    this._tree.innerHTML = `<div class="muted" style="padding:16px">Loading…</div>`;
    try {
      const schemas = await app.api.listSchemas(sid);
      this._tree.innerHTML = "";
      for (const s of schemas) {
        this._tree.appendChild(
          this._node({
            label: s.name,
            icon: "▦",
            badge: s.is_default ? "default" : null,
            loader: () => this._loadSchema(sid, s.name),
          })
        );
      }
    } catch (err) {
      this._tree.innerHTML = `<div class="muted" style="padding:16px">${escapeHtml(err.message)}</div>`;
    }
  }

  async _loadSchema(sid, schema) {
    const [tables, routines] = await Promise.all([
      app.api.listTables(sid, schema),
      app.api.listRoutines(sid, schema).catch(() => []),
    ]);
    const children = [];
    for (const t of tables) {
      children.push(
        this._node({
          label: t.name,
          icon: t.kind === "view" ? "◫" : "▤",
          badge: t.kind === "view" ? "view" : null,
          loader: () => this._loadTable(sid, schema, t.name),
        })
      );
    }
    if (routines.length) {
      const routineNodes = routines.map((r) =>
        this._leaf(`${r.kind === "procedure" ? "ƒ" : "λ"} ${escapeHtml(r.name)}`, r.return_type || "")
      );
      children.push(this._node({ label: "Routines", icon: "⚙", staticChildren: routineNodes }));
    }
    return children;
  }

  async _loadTable(sid, schema, table) {
    const detail = await app.api.describeTable(sid, table, schema);
    const cols = detail.columns.map((c) =>
      this._leaf(
        `${c.primary_key ? "🔑 " : ""}${escapeHtml(c.name)}`,
        `${escapeHtml(c.data_type)}${c.nullable ? "" : " · not null"}`
      )
    );
    const children = [this._node({ label: "Columns", icon: "≡", staticChildren: cols })];
    if (detail.indexes.length) {
      const idx = detail.indexes.map((i) =>
        this._leaf(escapeHtml(i.name), `${i.columns.join(", ")}${i.unique ? " · unique" : ""}`)
      );
      children.push(this._node({ label: "Indexes", icon: "⊞", staticChildren: idx }));
    }
    if (detail.foreign_keys.length) {
      const fks = detail.foreign_keys.map((f) =>
        this._leaf(
          `${f.columns.join(", ")} →`,
          `${f.referred_table} (${f.referred_columns.join(", ")})`
        )
      );
      children.push(this._node({ label: "Foreign keys", icon: "⇲", staticChildren: fks }));
    }
    return children;
  }

  // --- tree primitives -----------------------------------------------------------------

  _node({ label, icon, badge, loader, staticChildren }) {
    const el = document.createElement("div");
    el.className = "tree-node";
    el.innerHTML = `
      <div class="tree-row" tabindex="0">
        <span class="tree-caret">▸</span>
        <span class="tree-icon">${icon}</span>
        <span class="tree-label">${escapeHtml(label)}</span>
        ${badge ? `<span class="badge">${escapeHtml(badge)}</span>` : ""}
      </div>
      <div class="tree-children hidden"></div>`;
    const row = el.querySelector(".tree-row");
    const caret = el.querySelector(".tree-caret");
    const childBox = el.querySelector(".tree-children");
    let loaded = false;

    const toggle = async () => {
      const open = !childBox.classList.contains("hidden");
      if (open) {
        childBox.classList.add("hidden");
        caret.textContent = "▸";
        return;
      }
      caret.textContent = "▾";
      childBox.classList.remove("hidden");
      if (!loaded) {
        loaded = true;
        if (staticChildren) {
          staticChildren.forEach((c) => childBox.appendChild(c));
        } else if (loader) {
          childBox.innerHTML = `<div class="muted tree-loading">Loading…</div>`;
          try {
            const kids = await loader();
            childBox.innerHTML = "";
            (kids || []).forEach((c) => childBox.appendChild(c));
            if (!childBox.children.length)
              childBox.innerHTML = `<div class="muted tree-loading">— empty —</div>`;
          } catch (err) {
            childBox.innerHTML = `<div class="muted tree-loading">${escapeHtml(err.message)}</div>`;
          }
        }
      }
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter") toggle();
    });
    return el;
  }

  _leaf(label, meta) {
    const el = document.createElement("div");
    el.className = "tree-leaf";
    el.innerHTML = `<span class="tree-label mono">${label}</span>
      ${meta ? `<span class="muted tree-meta">${meta}</span>` : ""}`;
    return el;
  }
}

customElements.define("schema-view", SchemaView);
