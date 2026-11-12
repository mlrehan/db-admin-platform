// SQL IntelliSense engine (pure — no Monaco / DOM). Given the editor text, the cursor offset,
// the dialect and the database catalog, it works out WHAT the user is typing and returns ranked
// completion items:
//
//   SELECT * FROM A|           → tables/views starting with "A" (object context)
//   SELECT A.|                  → columns of table A (or of whatever alias A refers to)
//   SELECT t.| FROM Tutor t     → columns of Tutor (aliases are read from the WHOLE statement,
//                                 so the FROM clause may come after the cursor, like SSMS)
//   SELECT * FROM dbo.|         → objects in schema dbo
//   WHERE |  /  ON |  /  SET |  → columns of the tables in scope + functions + keywords
//   EXEC |  /  CALL |           → stored procedures / functions
//   INSERT INTO t (|            → columns of t
//
// The Monaco binding (components/sql-completion.js) maps these plain items to Monaco items.

import { builtinFunctions, functionSnippet } from "./sql-functions.js";

// --- dialect vocabulary --------------------------------------------------------------------

const COMMON_KEYWORDS = [
  "SELECT", "FROM", "WHERE", "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN",
  "CROSS JOIN", "ON", "GROUP BY", "ORDER BY", "HAVING", "INSERT INTO", "VALUES", "UPDATE", "SET",
  "DELETE FROM", "CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE VIEW", "CREATE INDEX",
  "CREATE UNIQUE INDEX", "CREATE PROCEDURE", "CREATE FUNCTION", "CREATE TRIGGER", "DROP VIEW",
  "DROP INDEX", "TRUNCATE TABLE", "DISTINCT", "AS", "AND", "OR", "NOT", "NULL", "IS NULL",
  "IS NOT NULL", "IN", "EXISTS", "BETWEEN", "LIKE", "CASE", "WHEN", "THEN", "ELSE", "END",
  "UNION", "UNION ALL", "EXCEPT", "INTERSECT", "WITH", "ASC", "DESC", "PRIMARY KEY",
  "FOREIGN KEY", "REFERENCES", "DEFAULT", "CHECK", "UNIQUE", "CONSTRAINT", "COMMIT", "ROLLBACK",
  "GRANT", "REVOKE", "ADD", "COLUMN", "INDEX", "VIEW", "TABLE", "OVER", "PARTITION BY",
];

const DIALECT_KEYWORDS = {
  mssql: [
    "TOP", "GO", "EXEC", "EXECUTE", "DECLARE", "PRINT", "BEGIN", "BEGIN TRAN", "BEGIN TRANSACTION",
    "COMMIT TRAN", "ROLLBACK TRAN", "SAVE TRAN", "BEGIN TRY", "END TRY", "BEGIN CATCH", "END CATCH",
    "OUTPUT", "MERGE", "USING", "MATCHED", "IDENTITY", "NOLOCK", "WITH (NOLOCK)", "OFFSET", "FETCH NEXT",
    "ROWS ONLY", "CROSS APPLY", "OUTER APPLY", "PIVOT", "UNPIVOT", "USE", "SET NOCOUNT ON",
    "CREATE OR ALTER PROCEDURE", "RAISERROR", "THROW", "IF", "WHILE", "RETURN", "NVARCHAR", "VARCHAR",
    "INT", "BIGINT", "BIT", "DATETIME2", "DECIMAL", "UNIQUEIDENTIFIER", "sp_executesql",
  ],
  postgresql: [
    "LIMIT", "OFFSET", "RETURNING", "ILIKE", "ON CONFLICT", "DO NOTHING", "DO UPDATE", "SERIAL",
    "BIGSERIAL", "GENERATED ALWAYS AS IDENTITY", "BEGIN", "START TRANSACTION", "SAVEPOINT",
    "CREATE OR REPLACE FUNCTION", "CREATE OR REPLACE VIEW", "LANGUAGE plpgsql", "CALL", "LATERAL",
    "FILTER", "WINDOW", "MATERIALIZED VIEW", "SCHEMA", "EXTENSION", "TEXT", "INTEGER", "BOOLEAN",
    "TIMESTAMPTZ", "JSONB", "UUID", "NUMERIC",
  ],
  mysql: [
    "LIMIT", "OFFSET", "AUTO_INCREMENT", "SHOW TABLES", "SHOW DATABASES", "DESCRIBE", "USE",
    "CALL", "DELIMITER", "ENGINE=InnoDB", "ON DUPLICATE KEY UPDATE", "REPLACE INTO",
    "START TRANSACTION", "BEGIN", "SAVEPOINT", "IF", "ELSEIF", "LOOP", "LEAVE", "ITERATE",
    "DECLARE", "INT", "BIGINT", "VARCHAR", "TEXT", "DATETIME", "DECIMAL", "JSON", "TINYINT",
  ],
  sqlite: [
    "LIMIT", "OFFSET", "AUTOINCREMENT", "PRAGMA", "WITHOUT ROWID", "ON CONFLICT", "INSERT OR REPLACE",
    "INSERT OR IGNORE", "BEGIN", "BEGIN TRANSACTION", "SAVEPOINT", "RELEASE", "ATTACH DATABASE",
    "VACUUM", "INTEGER", "TEXT", "REAL", "BLOB", "NUMERIC",
  ],
};

// Words that can never be a table alias (so "FROM t WHERE" doesn't treat WHERE as an alias).
const NON_ALIAS = new Set([
  "where", "join", "inner", "left", "right", "full", "outer", "cross", "on", "group", "order",
  "having", "union", "except", "intersect", "limit", "offset", "set", "values", "select", "from",
  "into", "using", "natural", "apply", "pivot", "unpivot", "with", "returning", "window", "fetch",
  "for", "when", "then", "else", "end", "go", "option", "output", "straight_join", "lateral",
]);
const RESERVED = new Set([
  ...NON_ALIAS, "table", "index", "view", "key", "primary", "foreign", "user", "check", "default",
  "column", "constraint", "create", "drop", "alter", "delete", "insert", "update", "grant",
  "order", "group", "by", "as", "and", "or", "not", "null", "is", "in", "like", "between", "case",
  "distinct", "all", "any", "exists", "top", "percent", "desc", "asc", "add", "references",
]);

const OBJECT_KWS = new Set(["from", "join", "update", "into", "table", "apply", "describe", "desc", "truncate", "merge"]);
const ROUTINE_KWS = new Set(["exec", "execute", "call"]);
const COLUMN_KWS = new Set([
  "select", "where", "on", "and", "or", "by", "having", "set", "when", "then", "else", "case",
  "not", "between", "like", "ilike", "distinct", "top", "returning", "over", "partition", "if",
  "is", "in", "using",
]);
const CLAUSE_STOP = new Set([...OBJECT_KWS, ...ROUTINE_KWS, ...COLUMN_KWS, "values", "as", "with", "group", "order"]);

export function quoteChars(dialect) {
  if (dialect === "mssql") return ["[", "]"];
  if (dialect === "mysql") return ["`", "`"];
  return ['"', '"'];
}

// --- tokenizer ------------------------------------------------------------------------------

/**
 * Tokenize SQL into identifiers, quoted identifiers, punctuation and operators. Strings,
 * numbers and comments are emitted as opaque tokens (comments are skipped). An unterminated
 * quoted identifier at the end (the user is typing `[Tu`) is emitted with `open: true`.
 */
export function tokenize(sql, dialect = "postgresql") {
  const tokens = [];
  const n = sql.length;
  const isMy = dialect === "mysql";
  const isMs = dialect === "mssql";
  let i = 0;
  while (i < n) {
    const ch = sql[i];
    if (ch === " " || ch === "\t" || ch === "\r" || ch === "\n") { i++; continue; }
    if (sql.startsWith("--", i) || (isMy && ch === "#")) {
      const eol = sql.indexOf("\n", i);
      i = eol === -1 ? n : eol;
      continue;
    }
    if (sql.startsWith("/*", i)) {
      let depth = 1;
      let j = i + 2;
      while (j < n && depth) {
        if (isMs && sql.startsWith("/*", j)) { depth++; j += 2; }
        else if (sql.startsWith("*/", j)) { depth--; j += 2; }
        else j++;
      }
      i = j;
      continue;
    }
    if (ch === "'") {
      let j = i + 1;
      while (j < n) {
        if (isMy && sql[j] === "\\") { j += 2; continue; }
        if (sql[j] === "'") { if (sql[j + 1] === "'") { j += 2; continue; } j++; break; }
        j++;
      }
      tokens.push({ type: "str", text: sql.slice(i, j), start: i, end: Math.min(j, n) });
      i = j;
      continue;
    }
    const qopen = ch === '"' || ch === "`" || (isMs && ch === "[");
    if (qopen) {
      const close = ch === "[" ? "]" : ch;
      let j = i + 1;
      let value = "";
      let closed = false;
      while (j < n) {
        if (sql[j] === close) {
          if (sql[j + 1] === close) { value += close; j += 2; continue; }
          closed = true;
          j++;
          break;
        }
        value += sql[j];
        j++;
      }
      tokens.push({ type: "ident", quoted: true, open: !closed, value, text: sql.slice(i, j), start: i, end: j });
      i = j;
      continue;
    }
    if (/[A-Za-z_@#]/.test(ch) || ch > "\u007f") {
      let j = i + 1;
      while (j < n && (/[A-Za-z0-9_$@#]/.test(sql[j]) || sql[j] > "\u007f")) j++;
      const text = sql.slice(i, j);
      tokens.push({ type: "ident", value: text, text, lower: text.toLowerCase(), start: i, end: j });
      i = j;
      continue;
    }
    if (/[0-9]/.test(ch)) {
      let j = i + 1;
      while (j < n && /[0-9.eE]/.test(sql[j])) j++;
      tokens.push({ type: "num", text: sql.slice(i, j), start: i, end: j });
      i = j;
      continue;
    }
    tokens.push({ type: "punct", text: ch, start: i, end: i + 1 });
    i++;
  }
  return tokens;
}

/** Bounds of the statement containing `offset` (splits on `;` and, for T-SQL, GO lines). */
export function statementBounds(text, offset, dialect = "postgresql") {
  let start = 0;
  let end = text.length;
  const toks = tokenize(text, dialect);
  for (const t of toks) {
    const isSemi = t.type === "punct" && t.text === ";";
    const isGo = dialect === "mssql" && t.type === "ident" && !t.quoted && t.lower === "go" &&
      lineIsOnly(text, t.start, t.end);
    if (!isSemi && !isGo) continue;
    if (t.end <= offset) start = t.end;
    else if (t.start >= offset) { end = t.start; break; }
  }
  return { start, end };
}

function lineIsOnly(text, s, e) {
  const ls = text.lastIndexOf("\n", s - 1) + 1;
  let le = text.indexOf("\n", e);
  if (le === -1) le = text.length;
  return text.slice(ls, s).trim() === "" && /^\s*(\d+)?\s*(--.*)?$/.test(text.slice(e, le));
}

// --- table references ------------------------------------------------------------------------

/** Read a dotted identifier chain starting at token index i. */
function readName(tokens, i) {
  const parts = [];
  let j = i;
  while (j < tokens.length && tokens[j].type === "ident" && !tokens[j].open) {
    parts.push(tokens[j].value);
    if (tokens[j + 1]?.text === "." && tokens[j + 2]?.type === "ident") j += 2;
    else { j++; break; }
  }
  return { parts, next: j };
}

/**
 * Every table reference in a statement: `FROM [db.][schema.]table [AS] alias`, JOINs, UPDATE,
 * INSERT INTO, MERGE, comma lists in FROM, and derived tables / CTEs (alias with no columns).
 */
export function tableRefs(tokens) {
  const refs = [];
  const ctes = new Set();
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type !== "ident" || t.quoted) continue;
    // WITH name AS ( … ) — common table expressions.
    if ((t.lower === "with" || (t.lower !== undefined && tokens[i - 1]?.text === ",")) &&
        tokens[i + 1]?.type === "ident" && tokens[i + 2]?.lower === "as" && tokens[i + 3]?.text === "(") {
      if (t.lower === "with") { ctes.add(tokens[i + 1].value.toLowerCase()); }
    }
    if (!OBJECT_KWS.has(t.lower) || t.lower === "table" && tokens[i - 1]?.lower !== "truncate") continue;
    let j = i + 1;
    // FROM a x, b y — read a comma-separated list for FROM.
    for (;;) {
      if (tokens[j]?.text === "(") {
        // Derived table: skip balanced parens, then take its alias.
        let depth = 0;
        for (; j < tokens.length; j++) {
          if (tokens[j].text === "(") depth++;
          else if (tokens[j].text === ")" && --depth === 0) { j++; break; }
        }
        const alias = readAlias(tokens, j);
        if (alias.name) refs.push({ schema: null, name: null, alias: alias.name, derived: true });
        j = alias.next;
      } else {
        const name = readName(tokens, j);
        if (!name.parts.length) break;
        const tname = name.parts[name.parts.length - 1];
        const schema = name.parts.length >= 2 ? name.parts[name.parts.length - 2] : null;
        const alias = readAlias(tokens, name.next);
        refs.push({ schema, name: tname, alias: alias.name, cte: ctes.has(tname.toLowerCase()) });
        j = alias.next;
      }
      if (t.lower === "from" && tokens[j]?.text === ",") { j++; continue; }
      break;
    }
  }
  return refs;
}

function readAlias(tokens, j) {
  let k = j;
  if (tokens[k]?.lower === "as") k++;
  const a = tokens[k];
  if (a && a.type === "ident" && !a.open && (a.quoted || !NON_ALIAS.has(a.lower))) {
    return { name: a.value, next: k + 1 };
  }
  return { name: null, next: j };
}

// --- context ---------------------------------------------------------------------------------

/**
 * Work out what is being typed at `offset`.
 * @returns {{kind:'member'|'object'|'routine'|'column'|'general'|'none', prefix:string,
 *            qualifier:string[], tables:object[], quoted:boolean, insertTable?:object}}
 */
export function analyzeContext(text, offset, dialect = "postgresql") {
  const { start, end } = statementBounds(text, offset, dialect);
  const stmt = text.slice(start, end);
  const cur = offset - start;
  const tokens = tokenize(stmt, dialect);
  const tables = tableRefs(tokens);

  // The partial word at the cursor (possibly inside an opening quote: `[Tu`).
  let idx = tokens.findIndex((t) => t.start < cur && t.end >= cur);
  let prefix = "";
  let quoted = false;
  let before;
  const inWord = idx !== -1 && tokens[idx].type === "ident" && tokens[idx].end === cur;
  if (inWord) {
    const tk = tokens[idx];
    quoted = !!tk.quoted;
    prefix = tk.quoted ? tk.value : stmt.slice(tk.start, cur);
    before = tokens.slice(0, idx);
  } else {
    if (idx !== -1 && (tokens[idx].type === "str")) return { kind: "none", prefix: "", qualifier: [], tables, quoted };
    before = tokens.filter((t) => t.end <= cur);
  }

  // Qualifier chain: `a.b.` immediately before the prefix.
  const qualifier = [];
  let k = before.length - 1;
  while (k >= 1 && before[k].text === "." && before[k - 1].type === "ident") {
    qualifier.unshift(before[k - 1].value);
    k -= 2;
  }
  if (qualifier.length) {
    // EXEC dbo.| / CALL app.| → only routines belong there (you can't EXEC a table).
    const lead = before[k];
    const routineOnly = !!lead && lead.type === "ident" && !lead.quoted && ROUTINE_KWS.has(lead.lower);
    return { kind: "member", prefix, qualifier, tables, quoted, routineOnly };
  }

  // Walk back to the governing clause keyword (skipping balanced parentheses).
  let depth = 0;
  let between = 0;
  for (let j = before.length - 1; j >= 0; j--) {
    const t = before[j];
    if (t.text === ")") { depth++; between++; continue; }
    if (t.text === "(") {
      if (depth > 0) { depth--; between++; continue; }
      // Unbalanced "(": INSERT INTO t ( … → columns of t.
      const name = backName(before, j - 1);
      if (name && before[name.start - 1]?.lower === "into") {
        return { kind: "column", prefix, qualifier, tables, quoted,
          insertTable: { schema: name.parts.length > 1 ? name.parts[name.parts.length - 2] : null,
                         name: name.parts[name.parts.length - 1] } };
      }
      return { kind: between === 0 ? "column" : "column", prefix, qualifier, tables, quoted };
    }
    if (depth > 0) continue;
    if (t.type === "ident" && !t.quoted && CLAUSE_STOP.has(t.lower)) {
      if (OBJECT_KWS.has(t.lower)) {
        const last = before[before.length - 1];
        if (between === 0 || last?.text === ",") return { kind: "object", prefix, qualifier, tables, quoted };
        if (last?.lower === "as") return { kind: "none", prefix, qualifier, tables, quoted };
        return { kind: "general", prefix, qualifier, tables, quoted };
      }
      if (ROUTINE_KWS.has(t.lower)) {
        return { kind: between === 0 ? "routine" : "general", prefix, qualifier, tables, quoted };
      }
      if (t.lower === "as") return { kind: between === 0 ? "none" : "general", prefix, qualifier, tables, quoted };
      return { kind: "column", prefix, qualifier, tables, quoted };
    }
    between++;
  }
  return { kind: "general", prefix, qualifier, tables, quoted };
}

function backName(tokens, j) {
  if (j < 0 || tokens[j]?.type !== "ident") return null;
  const parts = [tokens[j].value];
  let s = j;
  while (s >= 2 && tokens[s - 1].text === "." && tokens[s - 2].type === "ident") {
    parts.unshift(tokens[s - 2].value);
    s -= 2;
  }
  return { parts, start: s };
}

// --- catalog index ----------------------------------------------------------------------------

/** Pre-index a catalog response for O(1) lookups while typing. */
export function buildIndex(catalog) {
  const objects = catalog?.objects || [];
  const byName = new Map();
  const bySchema = new Map();
  for (const o of objects) {
    const key = o.name.toLowerCase();
    // Pre-lowercased once here, so filtering on every keystroke doesn't re-lowercase 20k names.
    Object.defineProperty(o, "_lower", { value: key, enumerable: false, configurable: true });
    if (!byName.has(key)) byName.set(key, []);
    byName.get(key).push(o);
    const sk = (o.schema_name || "").toLowerCase();
    if (!bySchema.has(sk)) bySchema.set(sk, []);
    bySchema.get(sk).push(o);
  }
  const routines = catalog?.routines || [];
  const routinesBySchema = new Map();
  for (const r of routines) {
    const sk = (r.schema_name || "").toLowerCase();
    if (!routinesBySchema.has(sk)) routinesBySchema.set(sk, []);
    routinesBySchema.get(sk).push(r);
  }
  return {
    objects,
    byName,
    bySchema,
    routines,
    routinesBySchema,
    schemas: [...new Set(objects.map((o) => o.schema_name).filter(Boolean))],
    defaultSchema: (catalog?.default_schema || "").toLowerCase(),
    dialect: catalog?.dialect || "postgresql",
  };
}

/** Find a catalog object by (optional schema, name), preferring the default schema. */
export function findObject(index, schema, name) {
  if (!name) return null;
  const list = index.byName.get(String(name).toLowerCase()) || [];
  if (!list.length) return null;
  if (schema) {
    return list.find((o) => (o.schema_name || "").toLowerCase() === String(schema).toLowerCase()) || null;
  }
  return list.find((o) => (o.schema_name || "").toLowerCase() === index.defaultSchema) || list[0];
}

/** Resolve a qualifier chain (`A.`, `dbo.`, `dbo.Tutor.`) → what to suggest. */
export function resolveMember(qualifier, tables, index) {
  const q = qualifier.map((s) => String(s).toLowerCase());
  const last = q[q.length - 1];
  if (q.length === 1) {
    // 1. An alias (or table name) used in this statement.
    const ref = tables.find((t) => (t.alias || "").toLowerCase() === last) ||
      tables.find((t) => !t.alias && (t.name || "").toLowerCase() === last) ||
      tables.find((t) => (t.name || "").toLowerCase() === last);
    if (ref) {
      const obj = ref.name ? findObject(index, ref.schema, ref.name) : null;
      if (obj) return { type: "columns", object: obj };
      // A real alias of a derived table / CTE: its columns aren't in the catalog.
      if ((ref.alias || "").toLowerCase() === last) return { type: "unknown" };
      // Otherwise it was just the half-typed name itself (e.g. "FROM sales.") — keep looking.
    }
    // 2. A table in the catalog (`SELECT Tutor.` before the FROM is written).
    const obj = findObject(index, null, last);
    if (obj) return { type: "columns", object: obj };
    // 3. A schema (or MySQL database) name.
    if (index.bySchema.has(last)) return { type: "objects", schema: last };
    return { type: "unknown" };
  }
  // schema.table.  /  db.schema.table.
  const obj = findObject(index, q[q.length - 2], last);
  if (obj) return { type: "columns", object: obj };
  if (index.bySchema.has(last)) return { type: "objects", schema: last }; // db.schema.
  return { type: "unknown" };
}

// --- suggestions --------------------------------------------------------------------------

export function needsQuote(name) {
  return !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || RESERVED.has(String(name).toLowerCase());
}

function ident(name, dialect, alreadyQuoted) {
  const [open, close] = quoteChars(dialect);
  if (alreadyQuoted) return name + close; // the user typed the opening quote
  return needsQuote(name) ? open + name.split(close).join(close + close) + close : name;
}

function matches(lowerName, lowerPrefix) {
  if (!lowerPrefix) return 0;
  if (lowerName.startsWith(lowerPrefix)) return 0;
  if (lowerName.includes(lowerPrefix)) return 1;
  return -1;
}

const MAX_ITEMS = 400;

/**
 * Build ranked completion items for an analysis. Items are plain objects:
 * {label, kind:'table'|'view'|'column'|'schema'|'routine'|'function'|'keyword'|'alias',
 *  insertText, snippet?, detail, sortText}. `incomplete` signals the list was capped.
 */
export function suggest(analysis, index, dialect) {
  const items = [];
  const seen = new Set();
  const { prefix, quoted } = analysis;
  const lowerPrefix = (prefix || "").toLowerCase();
  // sortText = rank, then match quality (prefix before substring), then either the item's
  // natural order (columns keep table-definition order: keys first) or its name.
  const push = (item, rank, order = null, lowerName = null) => {
    const m = matches(lowerName ?? (item.filterText || item.label).toLowerCase(), lowerPrefix);
    if (m < 0) return;
    const key = `${item.kind}:${item.label}`;
    if (seen.has(key)) return;
    seen.add(key);
    const tail = order == null ? item.label.toLowerCase() : String(order).padStart(5, "0");
    items.push({ ...item, sortText: `${rank}${m}${tail}` });
  };
  let tableSeq = 0;
  const columnsOf = (obj, rank, owner) => {
    const base = tableSeq++ * 1000;
    (obj.columns || []).forEach((c, i) => {
      push({
        label: c.name, kind: "column", insertText: ident(c.name, dialect, quoted),
        detail: `${c.type}${c.nullable ? "" : " NOT NULL"} — ${owner || obj.name}`,
      }, rank, base + i);
    });
  };
  const objectItem = (o, rank) => {
    // Cheap reject before building the item object (this runs for every table on large DBs).
    if (lowerPrefix && !(o._lower ?? o.name.toLowerCase()).includes(lowerPrefix)) return;
    const qualify = o.schema_name && index.defaultSchema &&
      o.schema_name.toLowerCase() !== index.defaultSchema && !quoted;
    push({
      label: o.name, filterText: o.name, kind: o.kind === "view" ? "view" : "table",
      insertText: qualify ? `${ident(o.schema_name, dialect)}.${ident(o.name, dialect)}` : ident(o.name, dialect, quoted),
      detail: `${o.kind === "view" ? "View" : "Table"}${o.schema_name ? ` · ${o.schema_name}` : ""} · ${(o.columns || []).length} columns`,
    }, rank, null, o._lower);
  };
  const objects = (rankDefault, rankOther) => {
    for (const o of index.objects) {
      const inDefault = !o.schema_name || o.schema_name.toLowerCase() === index.defaultSchema;
      objectItem(o, inDefault ? rankDefault : rankOther);
      if (items.length > MAX_ITEMS) break;
    }
  };
  const routines = (rank, schema) => {
    const list = schema != null ? index.routinesBySchema.get(schema) || [] : index.routines;
    for (const r of list) {
      const args = Array.isArray(r.params) ? `(${r.params.join(", ")})` : "";
      push({ label: r.name, kind: "routine", insertText: ident(r.name, dialect, quoted),
        detail: `${r.kind === "procedure" ? "Stored procedure" : "Function"}${r.schema_name ? ` · ${r.schema_name}` : ""}${args ? ` ${args}` : ""}${r.returns ? ` → ${r.returns}` : ""}`,
        routine: r }, rank);
    }
  };
  const schemas = (rank) => {
    for (const s of index.schemas) {
      push({ label: s, kind: "schema", insertText: ident(s, dialect, quoted), detail: "Schema" }, rank);
    }
  };
  const keywords = (rank) => {
    for (const k of [...COMMON_KEYWORDS, ...(DIALECT_KEYWORDS[dialect] || [])]) {
      push({ label: k, kind: "keyword", insertText: k, detail: "Keyword" }, rank);
    }
  };
  const functions = (rank) => {
    // Full built-in catalog for the dialect (core/sql-functions.js): the item inserts named
    // placeholders for the required parameters and carries every overload for the docs panel.
    for (const [name, sigs] of builtinFunctions(dialect)) {
      push({ label: name, kind: "function", insertText: functionSnippet(sigs[0]), snippet: true,
        detail: sigs[0].label, signatures: sigs }, rank);
    }
  };
  const inScope = () =>
    analysis.tables
      .map((t) => ({ ref: t, obj: t.name ? findObject(index, t.schema, t.name) : null }))
      .filter((x) => x.obj);

  switch (analysis.kind) {
    case "member": {
      const target = resolveMember(analysis.qualifier, analysis.tables, index);
      if (target.type === "columns") {
        push({ label: "*", kind: "column", insertText: "*", detail: `All columns of ${target.object.name}` }, 0);
        columnsOf(target.object, 1);
      } else if (target.type === "objects" && analysis.routineOnly) {
        routines(0, target.schema);
      } else if (target.type === "objects") {
        for (const o of index.bySchema.get(target.schema) || []) {
          push({ label: o.name, kind: o.kind === "view" ? "view" : "table", insertText: ident(o.name, dialect, quoted),
            detail: `${o.kind === "view" ? "View" : "Table"} · ${(o.columns || []).length} columns` }, 0);
        }
        routines(1, target.schema);
      }
      break;
    }
    case "object":
      objects(0, 1);
      schemas(2);
      break;
    case "routine":
      routines(0);
      schemas(2);
      break;
    case "column": {
      if (analysis.insertTable) {
        const obj = findObject(index, analysis.insertTable.schema, analysis.insertTable.name);
        if (obj) columnsOf(obj, 0);
        break;
      }
      const scope = inScope();
      for (const { ref, obj } of scope) columnsOf(obj, 0, ref.alias ? `${ref.alias} (${obj.name})` : obj.name);
      for (const { ref } of [...analysis.tables.map((ref) => ({ ref }))]) {
        if (ref.alias) push({ label: ref.alias, kind: "alias", insertText: ref.alias, detail: `Alias for ${ref.name || "derived table"}` }, 1);
      }
      functions(2);
      keywords(3);
      if (!scope.length) objects(4, 5); // nothing in FROM yet → still offer tables
      break;
    }
    case "general":
      keywords(0);
      functions(1);
      objects(2, 3);
      break;
    default:
      break;
  }
  items.sort((a, b) => (a.sortText < b.sortText ? -1 : a.sortText > b.sortText ? 1 : 0));
  const incomplete = items.length > MAX_ITEMS;
  return { items: items.slice(0, MAX_ITEMS), incomplete };
}

/** Convenience: analyze + suggest in one call. */
export function complete(text, offset, index, dialect) {
  const analysis = analyzeContext(text, offset, dialect);
  return { analysis, ...suggest(analysis, index, dialect) };
}
