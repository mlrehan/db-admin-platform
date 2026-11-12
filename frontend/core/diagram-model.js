// Database diagram model — the single source of truth for what is on the canvas (pure: no DOM).
//
// Root cause of the old "double-click adds a table twice / added tables can't be removed" bug:
// the view checked "is it already on the canvas?" and only THEN awaited the table metadata, so
// two quick clicks both passed the check and created two DOM boxes; the bookkeeping map kept
// only the second, leaving the first as an orphan nothing could reach.
//
// Here, adding a table RESERVES its node synchronously (status "loading") before any await, so
// every later add of the same table — click, double-click, drag-and-drop, restore — is a no-op.
// Table identity is case-insensitive (schema.table), the view renders strictly FROM this model
// (reconciling its DOM against `nodes`), and removal deletes the node, which removes its box.

export function tableKey(schema, name) {
  return `${String(schema || "").toLowerCase()}.${String(name || "").toLowerCase()}`;
}

function relId(r) {
  const child = tableKey(r.child_schema, r.child_table);
  const parent = tableKey(r.parent_schema, r.parent_table);
  return r.name
    ? `fk:${child}:${String(r.name).toLowerCase()}`
    : `fk:${child}(${r.child_columns.join(",")})->${parent}(${r.parent_columns.join(",")})`.toLowerCase();
}

export class DiagramModel {
  constructor() {
    this.nodes = new Map(); // key -> { key, schema, name, x, y, status, detail }
    this.hidden = new Set(); // relationship ids hidden from the diagram (diagram-only)
    this.relationships = [];
    this.junctions = [];
    this._listeners = new Set();
  }

  onChange(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  _emit(reason) {
    for (const fn of this._listeners) fn(reason);
  }

  has(key) {
    return this.nodes.has(key);
  }

  /**
   * Reserve a table on the canvas. Synchronous and idempotent: returns `added: false` if the
   * table is already present (or still loading), so callers never create duplicates.
   */
  reserve(schema, name, at = null) {
    const key = tableKey(schema, name);
    if (this.nodes.has(key)) return { key, added: false };
    const pos = at || this._nextSlot();
    this.nodes.set(key, {
      key, schema: schema || null, name, x: Math.round(pos.x), y: Math.round(pos.y), status: "loading", detail: null,
    });
    this._emit("add");
    return { key, added: true };
  }

  /** Attach loaded metadata to a reserved node (ignored if it was removed meanwhile). */
  resolve(key, detail) {
    const node = this.nodes.get(key);
    if (!node) return false;
    node.detail = detail;
    node.status = "ready";
    this._emit("resolve");
    return true;
  }

  /** A table whose metadata failed to load is dropped (no ghost boxes). */
  fail(key) {
    if (this.nodes.delete(key)) this._emit("remove");
  }

  remove(key) {
    const removed = this.nodes.delete(key);
    if (removed) this._emit("remove");
    return removed;
  }

  clear() {
    if (!this.nodes.size) return;
    this.nodes.clear();
    this._emit("clear");
  }

  move(key, x, y) {
    const node = this.nodes.get(key);
    if (!node) return;
    node.x = Math.round(x);
    node.y = Math.round(y);
  }

  setRelationships(graph) {
    this.relationships = graph?.relationships || [];
    this.junctions = graph?.junctions || [];
    this._emit("relationships");
  }

  isJunction(key) {
    return this.junctions.some((j) => tableKey(j.schema_name, j.table) === key);
  }

  /**
   * Connectors between tables currently on the canvas:
   *  • every FK whose child and parent are both present (1:1 / 1:N, self-references included);
   *  • a logical many-to-many connector between two present tables when the junction table that
   *    links them is NOT on the canvas (when it is, its two 1:N FKs are drawn instead, as SSMS does).
   */
  edges() {
    const out = [];
    for (const r of this.relationships) {
      const child = tableKey(r.child_schema, r.child_table);
      const parent = tableKey(r.parent_schema, r.parent_table);
      const id = relId(r);
      if (!this.nodes.has(child) || !this.nodes.has(parent) || this.hidden.has(id)) continue;
      out.push({
        id, kind: r.cardinality, name: r.name, child, parent, self: child === parent,
        childColumns: r.child_columns, parentColumns: r.parent_columns,
        onDelete: r.on_delete, onUpdate: r.on_update, relationship: r,
      });
    }
    for (const j of this.junctions) {
      const jKey = tableKey(j.schema_name, j.table);
      if (this.nodes.has(jKey)) continue;
      const present = j.parents.map(([s, t]) => tableKey(s, t)).filter((k) => this.nodes.has(k));
      for (let a = 0; a < present.length; a++) {
        for (let b = a + 1; b < present.length; b++) {
          const id = `mn:${jKey}:${present[a]}:${present[b]}`;
          if (this.hidden.has(id)) continue;
          out.push({ id, kind: "many-to-many", via: j.table, child: present[a], parent: present[b], self: false });
        }
      }
    }
    return out;
  }

  hideEdge(id) {
    this.hidden.add(id);
    this._emit("hide");
  }

  showAllEdges() {
    this.hidden.clear();
    this._emit("hide");
  }

  /** Tables directly related to `key` (both directions, plus junction partners). */
  relatedKeys(key) {
    const related = new Set();
    for (const r of this.relationships) {
      const child = tableKey(r.child_schema, r.child_table);
      const parent = tableKey(r.parent_schema, r.parent_table);
      if (child === key && parent !== key) related.add(parent);
      if (parent === key && child !== key) related.add(child);
    }
    return [...related];
  }

  /** Next free grid slot for a table added without an explicit position. */
  _nextSlot() {
    const i = this.nodes.size;
    return { x: 40 + (i % 4) * 280, y: 40 + Math.floor(i / 4) * 260 };
  }

  /**
   * Layered auto-layout: referenced (parent) tables on the left, referencing tables to their
   * right, so connectors mostly run one way. `sizes` maps key → {w, h} measured from the DOM.
   */
  autoLayout(sizes = new Map(), gapX = 90, gapY = 40) {
    const keys = [...this.nodes.keys()];
    const parentsOf = new Map(keys.map((k) => [k, new Set()]));
    for (const e of this.edges()) {
      if (!e.self && e.kind !== "many-to-many") parentsOf.get(e.child)?.add(e.parent);
    }
    const level = new Map();
    const depth = (k, seen = new Set()) => {
      if (level.has(k)) return level.get(k);
      if (seen.has(k)) return 0; // cycle guard
      seen.add(k);
      let d = 0;
      for (const p of parentsOf.get(k) || []) d = Math.max(d, depth(p, seen) + 1);
      level.set(k, d);
      return d;
    };
    keys.forEach((k) => depth(k));
    const columns = [];
    for (const k of keys) (columns[level.get(k)] ||= []).push(k);
    let x = 40;
    for (const col of columns) {
      if (!col) continue;
      col.sort();
      let y = 40;
      let width = 0;
      for (const k of col) {
        const s = sizes.get(k) || { w: 240, h: 200 };
        this.move(k, x, y);
        y += s.h + gapY;
        width = Math.max(width, s.w);
      }
      x += width + gapX;
    }
    this._emit("layout");
  }

  serialize() {
    return {
      nodes: [...this.nodes.values()].map((n) => ({ schema: n.schema, name: n.name, x: n.x, y: n.y })),
      hidden: [...this.hidden],
    };
  }
}

// --- relationship-creation helpers (pure) ---------------------------------------------------

const TYPE_ALIASES = {
  integer: "int", int4: "int", int: "int", int8: "bigint", bigint: "bigint", int2: "smallint",
  smallint: "smallint", "character varying": "varchar", varchar: "varchar", nvarchar: "nvarchar",
  character: "char", char: "char", nchar: "nchar", bool: "boolean", boolean: "boolean",
  decimal: "numeric", numeric: "numeric", text: "text", uuid: "uuid", uniqueidentifier: "uuid",
};

/** Base type without length/precision/collation, normalised across dialects ("INT" == "integer"). */
export function baseType(type) {
  const t = String(type || "").toLowerCase()
    .replace(/\s+collate\s+.*$/i, "")
    .replace(/\(.*\)/, "")
    .replace(/\s+unsigned\b/, "")
    .trim();
  return TYPE_ALIASES[t] || t;
}

/** Strip noise like COLLATE clauses from a reflected type for display. */
export function displayType(type) {
  return String(type || "").replace(/\s+COLLATE\s+("[^"]*"|\S+)/gi, "").replace(/\s+/g, " ").trim();
}

function quoteIdent(dialect, name) {
  const s = String(name);
  if (dialect === "mssql") return `[${s.replace(/]/g, "]]")}]`;
  if (dialect === "mysql") return "`" + s.replace(/`/g, "``") + "`";
  return `"${s.replace(/"/g, '""')}"`;
}

function qualified(dialect, schema, table) {
  return schema && dialect !== "sqlite" && dialect !== "mysql"
    ? `${quoteIdent(dialect, schema)}.${quoteIdent(dialect, table)}`
    : quoteIdent(dialect, table);
}

/**
 * Decide parent/child for a column dragged onto another column and validate it.
 * Each end: { schema, table, column, type, isKey } where isKey = PK or unique single column.
 */
export function planRelationship(source, target) {
  const problems = [];
  let parent;
  let child;
  if (target.isKey && !source.isKey) [parent, child] = [target, source];
  else if (source.isKey && !target.isKey) [parent, child] = [source, target];
  else if (target.isKey && source.isKey) [parent, child] = [target, source];
  else {
    return { ok: false, problems: ["One of the two columns must be a primary key or a unique column (the referenced side)."] };
  }
  if (tableKey(parent.schema, parent.table) === tableKey(child.schema, child.table) &&
      parent.column.toLowerCase() === child.column.toLowerCase()) {
    return { ok: false, problems: ["A column cannot reference itself."] };
  }
  const warnings = [];
  if (baseType(parent.type) !== baseType(child.type)) {
    warnings.push(`Data types differ (${displayType(child.type)} → ${displayType(parent.type)}); the database may reject this.`);
  }
  return { ok: true, parent, child, problems, warnings };
}

export function defaultConstraintName(child, parent) {
  return `FK_${child.table}_${parent.table}`.replace(/[^A-Za-z0-9_]/g, "_").slice(0, 120);
}

const ACTIONS = new Set(["NO ACTION", "CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT"]);

/** ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY for the dialect (null for SQLite: unsupported). */
export function addForeignKeySql(dialect, { child, parent, name, onDelete, onUpdate }) {
  if (dialect === "sqlite") return null; // SQLite can't add a FK to an existing table
  let sql = `ALTER TABLE ${qualified(dialect, child.schema, child.table)}\n` +
    `  ADD CONSTRAINT ${quoteIdent(dialect, name)} FOREIGN KEY (${quoteIdent(dialect, child.column)})\n` +
    `  REFERENCES ${qualified(dialect, parent.schema, parent.table)} (${quoteIdent(dialect, parent.column)})`;
  if (onDelete && ACTIONS.has(onDelete) && onDelete !== "NO ACTION") sql += ` ON DELETE ${onDelete}`;
  if (onUpdate && ACTIONS.has(onUpdate) && onUpdate !== "NO ACTION") sql += ` ON UPDATE ${onUpdate}`;
  return `${sql};`;
}

/** ALTER TABLE … DROP CONSTRAINT / DROP FOREIGN KEY (null when it can't be expressed). */
export function dropForeignKeySql(dialect, { childSchema, childTable, name }) {
  if (!name || dialect === "sqlite") return null;
  const table = qualified(dialect, childSchema, childTable);
  return dialect === "mysql"
    ? `ALTER TABLE ${table} DROP FOREIGN KEY ${quoteIdent(dialect, name)};`
    : `ALTER TABLE ${table} DROP CONSTRAINT ${quoteIdent(dialect, name)};`;
}
