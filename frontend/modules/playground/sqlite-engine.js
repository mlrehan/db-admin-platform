// Isolated client-side SQLite engine for the public SQL Playground.
//
// Architectural guarantees:
//   • Runs 100% in browser memory using SQLite compiled to WebAssembly (sql.js) — or Node's
//     built-in `node:sqlite` (`DatabaseSync(':memory:')`) when executed in Node unit tests.
//   • Never imports or calls the enterprise FastAPI backend, HTTP client, WebSocket client,
//     AuthService, or ConnectionOrchestrator session store.
//   • Deterministic schema & seed dataset (`course`, `student`, `admission`) with foreign-key
//     enforcement (`PRAGMA foreign_keys = ON`).

import { config } from "../../core/config.js";

export const INITIAL_PLAYGROUND_QUERY = "SELECT * FROM student;";

export const MAX_PLAYGROUND_ROWS = 5000;

export const SAMPLE_QUERIES = [
  {
    id: "select-students",
    label: "All Students (Default)",
    sql: "SELECT * FROM student;",
  },
  {
    id: "filter-sort",
    label: "Filter & Sort Students",
    sql: "SELECT id, name, age\nFROM student\nWHERE age >= 17\nORDER BY age DESC, name ASC;",
  },
  {
    id: "join-admissions",
    label: "Student Course Enrollments (JOIN)",
    sql: `SELECT
  s.name AS student_name,
  s.age,
  c.name AS course_name,
  a.admission_date
FROM admission a
JOIN student s ON a.student_id = s.id
JOIN course c ON a.course_id = c.id
ORDER BY a.admission_date ASC, s.name ASC;`,
  },
  {
    id: "course-counts",
    label: "Enrollment Count by Course (GROUP BY)",
    sql: `SELECT
  c.id,
  c.name AS course_name,
  COUNT(a.id) AS enrolled_students
FROM course c
LEFT JOIN admission a ON c.id = a.course_id
GROUP BY c.id, c.name
ORDER BY enrolled_students DESC, c.name ASC;`,
  },
  {
    id: "insert-student",
    label: "Insert a New Student (DML)",
    sql: `INSERT INTO student (id, name, age) VALUES (9, 'Sophia Patel', 18);
SELECT * FROM student ORDER BY id;`,
  },
];

// Deterministic seed statements for the three required tables.
export const SEED_STATEMENTS = [
  "PRAGMA foreign_keys = ON;",
  "DROP TABLE IF EXISTS admission;",
  "DROP TABLE IF EXISTS student;",
  "DROP TABLE IF EXISTS course;",
  `CREATE TABLE course (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
  );`,
  `CREATE TABLE student (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    age INTEGER
  );`,
  `CREATE TABLE admission (
    id INTEGER PRIMARY KEY,
    student_id INTEGER,
    course_id INTEGER,
    admission_date TEXT,
    FOREIGN KEY (student_id) REFERENCES student(id),
    FOREIGN KEY (course_id) REFERENCES course(id)
  );`,
  `INSERT INTO course (id, name) VALUES
    (1, 'Mathematics'),
    (2, 'Physics'),
    (3, 'Chemistry'),
    (4, 'Biology'),
    (5, 'Computer Science');`,
  `INSERT INTO student (id, name, age) VALUES
    (1, 'Alice Johnson', 17),
    (2, 'Bob Smith', 16),
    (3, 'Carol Davis', 18),
    (4, 'David Wilson', 17),
    (5, 'Emma Brown', 16),
    (6, 'Frank Miller', 18),
    (7, 'Grace Lee', 17),
    (8, 'Henry Taylor', 16);`,
  `INSERT INTO admission (id, student_id, course_id, admission_date) VALUES
    (1, 1, 1, '2024-09-01'),
    (2, 1, 5, '2024-09-01'),
    (3, 2, 2, '2024-09-03'),
    (4, 2, 3, '2024-09-03'),
    (5, 3, 1, '2024-09-03'),
    (6, 3, 4, '2024-09-03'),
    (7, 4, 5, '2024-09-06'),
    (8, 5, 2, '2024-09-08'),
    (9, 6, 3, '2024-09-16'),
    (10, 7, 4, '2024-09-16'),
    (11, 8, 1, '2024-09-28'),
    (12, 8, 5, '2024-09-28');`,
];

const FALLBACK_SQLJS_BASES = [
  config.sqlJsBase,
  "https://cdn.jsdelivr.net/npm/sql.js@1.12.0/dist",
  "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0",
];

let _sqlJsPromise = null;

function loadScriptFromBase(baseUrl) {
  return new Promise((resolve, reject) => {
    if (window.initSqlJs) {
      resolve({ initSqlJs: window.initSqlJs, baseUrl });
      return;
    }
    const timer = setTimeout(() => reject(new Error(`Timeout loading sql.js from ${baseUrl}`)), 10000);
    const script = document.createElement("script");
    script.src = `${baseUrl.replace(/\/$/, "")}/sql-wasm.js`;
    script.onload = () => {
      clearTimeout(timer);
      if (typeof window.initSqlJs === "function") {
        resolve({ initSqlJs: window.initSqlJs, baseUrl: baseUrl.replace(/\/$/, "") });
      } else {
        reject(new Error("initSqlJs global not found after script load"));
      }
    };
    script.onerror = () => {
      clearTimeout(timer);
      script.remove();
      reject(new Error(`Failed to load sql-wasm.js from ${baseUrl}`));
    };
    document.head.appendChild(script);
  });
}

// Adapter wrapping Node's built-in `node:sqlite` (`DatabaseSync(':memory:')`) with the same
// statement API as sql.js so `PlaygroundDatabase` can be unit-tested in Node with zero npm deps.
async function loadNodeSqliteAdapter() {
  const { DatabaseSync } = await import("node:sqlite");
  class NodeSqliteDatabase {
    constructor() {
      this._db = new DatabaseSync(":memory:");
      this._lastChanges = 0;
    }
    run(sql) {
      this._db.exec(sql);
    }
    prepare(sql) {
      const stmt = this._db.prepare(sql);
      const colMeta = stmt.columns();
      const colNames = colMeta.map((c) => c.name);
      let rows = null;
      let idx = 0;
      const parent = this;
      return {
        getColumnNames() {
          return colNames;
        },
        step() {
          if (rows === null) {
            if (colNames.length > 0) {
              stmt.setReturnArrays(true);
              rows = stmt.all();
              parent._lastChanges = 0;
            } else {
              const res = stmt.run();
              parent._lastChanges = Number(res?.changes ?? 0);
              rows = [];
            }
          }
          return idx < rows.length;
        },
        get() {
          return rows[idx++];
        },
        free() {
          /* GC managed by node:sqlite */
        },
      };
    }
    getRowsModified() {
      return this._lastChanges;
    }
    close() {
      this._db.close();
    }
  }
  return { Database: NodeSqliteDatabase };
}

/**
 * Load the SQLite engine module (`{ Database }`).
 * Uses WebAssembly `sql.js` in the browser and built-in `node:sqlite` in Node tests.
 */
export async function loadSqliteEngine() {
  if (_sqlJsPromise) return _sqlJsPromise;

  if (typeof window === "undefined") {
    _sqlJsPromise = loadNodeSqliteAdapter();
    return _sqlJsPromise;
  }

  _sqlJsPromise = (async () => {
    const bases = [...new Set(FALLBACK_SQLJS_BASES.filter(Boolean))];
    let lastErr = null;
    for (const base of bases) {
      try {
        const { initSqlJs, baseUrl } = await loadScriptFromBase(base);
        const SQL = await initSqlJs({
          locateFile: (file) => `${baseUrl}/${file}`,
        });
        return SQL;
      } catch (err) {
        lastErr = err;
        window.initSqlJs = undefined;
      }
    }
    _sqlJsPromise = null;
    throw lastErr || new Error("Could not initialize browser SQLite engine.");
  })();

  return _sqlJsPromise;
}

/**
 * Split a SQL script into individual statements while respecting single-quoted strings,
 * double-quoted identifiers, backtick identifiers, bracket identifiers, line comments (`--`),
 * and block comments (`/* ... * /`).
 */
export function splitSqlStatements(sql) {
  const text = String(sql ?? "");
  const statements = [];
  let current = "";
  let i = 0;
  const len = text.length;

  while (i < len) {
    const ch = text[i];
    const next = text[i + 1];

    // Line comment: -- ... \n
    if (ch === "-" && next === "-") {
      let end = text.indexOf("\n", i + 2);
      if (end === -1) end = len;
      i = end + 1;
      current += "\n";
      continue;
    }

    // Block comment: /* ... */
    if (ch === "/" && next === "*") {
      const end = text.indexOf("*/", i + 2);
      if (end === -1) {
        // Unclosed block comment: pass through so SQLite reports the syntax error.
        current += text.slice(i);
        break;
      }
      i = end + 2;
      current += " ";
      continue;
    }

    // Single-quoted string literal ('...' with '' escaping)
    if (ch === "'") {
      current += ch;
      i++;
      while (i < len) {
        const c = text[i];
        current += c;
        i++;
        if (c === "'") {
          if (text[i] === "'") {
            current += text[i];
            i++;
          } else {
            break;
          }
        }
      }
      continue;
    }

    // Double-quoted or backtick identifier
    if (ch === '"' || ch === "`") {
      const quote = ch;
      current += ch;
      i++;
      while (i < len) {
        const c = text[i];
        current += c;
        i++;
        if (c === quote) {
          if (text[i] === quote) {
            current += text[i];
            i++;
          } else {
            break;
          }
        }
      }
      continue;
    }

    // Bracket-quoted identifier [...]
    if (ch === "[") {
      current += ch;
      i++;
      while (i < len) {
        const c = text[i];
        current += c;
        i++;
        if (c === "]") break;
      }
      continue;
    }

    // Statement terminator
    if (ch === ";") {
      const trimmed = current.trim();
      if (trimmed) statements.push(trimmed);
      current = "";
      i++;
      continue;
    }

    current += ch;
    i++;
  }

  const tail = current.trim();
  if (tail) statements.push(tail);
  return statements;
}

function classifyStatement(stmtSql) {
  const firstWord = String(stmtSql || "")
    .trim()
    .split(/\s+/)[0]
    ?.toUpperCase();
  if (["SELECT", "PRAGMA", "EXPLAIN", "VALUES", "WITH"].includes(firstWord)) return "read";
  if (["INSERT", "UPDATE", "DELETE", "REPLACE"].includes(firstWord)) return "write";
  if (["CREATE", "ALTER", "DROP", "REINDEX", "VACUUM"].includes(firstWord)) return "ddl";
  return "other";
}

/**
 * Isolated in-memory SQLite Playground database controller.
 */
export class PlaygroundDatabase {
  /**
   * @param {object} [opts]
   * @param {object} [opts.engine] Optional injected SQLite engine (`{ Database }`) for testing.
   */
  constructor({ engine = null } = {}) {
    this._engine = engine;
    this._db = null;
    this._running = false;
  }

  get isReady() {
    return Boolean(this._db);
  }

  get isRunning() {
    return this._running;
  }

  async init() {
    if (!this._engine) {
      this._engine = await loadSqliteEngine();
    }
    this._recreateAndSeed();
    return this;
  }

  _recreateAndSeed() {
    if (this._db) {
      try {
        this._db.close();
      } catch {
        /* ignore close errors on reset */
      }
    }
    this._db = new this._engine.Database();
    for (const stmt of SEED_STATEMENTS) {
      this._db.run(stmt);
    }
  }

  /**
   * Restore the database to its exact initial schema and deterministic sample records.
   */
  reset() {
    if (!this._engine) {
      throw new Error("SQLite engine is not initialized yet.");
    }
    this._recreateAndSeed();
    return this.getSchema();
  }

  /**
   * Execute one or more SQL statements against the isolated in-memory SQLite instance.
   *
   * @param {string} rawSql
   * @returns {{
   *   ok: boolean,
   *   empty?: boolean,
   *   error?: string,
   *   durationMs: number,
   *   statements: Array<{
   *     sql: string,
   *     category: string,
   *     returnsRows: boolean,
   *     columns: string[],
   *     rows: any[][],
   *     rowCount: number,
   *     totalRowCount: number,
   *     truncated: boolean,
   *     rowsAffected: number
   *   }>,
   *   resultSets: Array<any>,
   *   totalRowsReturned: number,
   *   totalRowsAffected: number
   * }}
   */
  execute(rawSql) {
    const start = typeof performance !== "undefined" ? performance.now() : Date.now();
    const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

    if (!this._db) {
      return {
        ok: false,
        error: "Database is still initializing. Please wait a moment and try again.",
        durationMs: 0,
        statements: [],
        resultSets: [],
        totalRowsReturned: 0,
        totalRowsAffected: 0,
      };
    }

    if (this._running) {
      return {
        ok: false,
        error: "A query is already running. Please wait for it to finish.",
        durationMs: 0,
        statements: [],
        resultSets: [],
        totalRowsReturned: 0,
        totalRowsAffected: 0,
      };
    }

    const statementsSql = splitSqlStatements(rawSql);
    if (statementsSql.length === 0) {
      return {
        ok: false,
        empty: true,
        error: "Please enter a SQL query to execute.",
        durationMs: 0,
        statements: [],
        resultSets: [],
        totalRowsReturned: 0,
        totalRowsAffected: 0,
      };
    }

    this._running = true;
    const statements = [];
    try {
      for (const stmtSql of statementsSql) {
        const category = classifyStatement(stmtSql);
        let stmt = null;
        try {
          stmt = this._db.prepare(stmtSql);
          const allRows = [];
          while (stmt.step()) {
            allRows.push(stmt.get());
          }
          const columns = stmt.getColumnNames ? stmt.getColumnNames() : [];
          const returnsRows = columns.length > 0;
          const totalRowCount = allRows.length;
          const truncated = totalRowCount > MAX_PLAYGROUND_ROWS;
          const rows = truncated ? allRows.slice(0, MAX_PLAYGROUND_ROWS) : allRows;
          // SQLite's sqlite3_changes() only updates on INSERT/UPDATE/DELETE/REPLACE; DDL leaves
          // the previous count untouched, so only read getRowsModified() for write statements.
          const rowsAffected =
            !returnsRows && category === "write"
              ? Number(this._db.getRowsModified?.() ?? 0)
              : 0;

          statements.push({
            sql: stmtSql,
            category,
            returnsRows,
            columns,
            rows,
            rowCount: rows.length,
            totalRowCount,
            truncated,
            rowsAffected,
          });
        } finally {
          stmt?.free?.();
        }
      }

      const resultSets = statements.filter((s) => s.returnsRows);
      const totalRowsReturned = resultSets.reduce((sum, s) => sum + s.totalRowCount, 0);
      const totalRowsAffected = statements
        .filter((s) => !s.returnsRows)
        .reduce((sum, s) => sum + s.rowsAffected, 0);

      return {
        ok: true,
        durationMs: Math.max(0, Math.round((now() - start) * 10) / 10),
        statements,
        resultSets,
        totalRowsReturned,
        totalRowsAffected,
      };
    } catch (err) {
      return {
        ok: false,
        error: err?.message || String(err),
        durationMs: Math.max(0, Math.round((now() - start) * 10) / 10),
        statements,
        resultSets: statements.filter((s) => s.returnsRows),
        totalRowsReturned: 0,
        totalRowsAffected: 0,
      };
    } finally {
      this._running = false;
    }
  }

  /**
   * Introspect the live SQLite schema (tables, columns, types, primary keys, foreign keys, and row counts).
   */
  getSchema() {
    if (!this._db) return [];
    const tables = [];
    let listStmt = null;
    try {
      listStmt = this._db.prepare(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY CASE name WHEN 'course' THEN 1 WHEN 'student' THEN 2 WHEN 'admission' THEN 3 ELSE 4 END, name ASC;"
      );
      const tableEntries = [];
      while (listStmt.step()) {
        const [name, type] = listStmt.get();
        tableEntries.push({ name, type });
      }
      listStmt.free();
      listStmt = null;

      for (const entry of tableEntries) {
        const safeIdent = `"${String(entry.name).replace(/"/g, '""')}"`;

        // Foreign keys map: column_name -> { table, to }
        const fkMap = new Map();
        let fkStmt = null;
        try {
          fkStmt = this._db.prepare(`PRAGMA foreign_key_list(${safeIdent});`);
          while (fkStmt.step()) {
            const row = fkStmt.get();
            // PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, match
            fkMap.set(row[3], { referredTable: row[2], referredColumn: row[4] });
          }
        } catch {
          /* ignore pragma error on views */
        } finally {
          fkStmt?.free?.();
        }

        // Columns: cid, name, type, notnull, dflt_value, pk
        const columns = [];
        let colStmt = null;
        try {
          colStmt = this._db.prepare(`PRAGMA table_info(${safeIdent});`);
          while (colStmt.step()) {
            const row = colStmt.get();
            const colName = row[1];
            const fk = fkMap.get(colName) || null;
            columns.push({
              name: colName,
              type: row[2] || "ANY",
              notNull: Boolean(row[3]),
              defaultValue: row[4],
              primaryKey: Number(row[5]) > 0,
              foreignKey: fk,
            });
          }
        } finally {
          colStmt?.free?.();
        }

        // Row count
        let rowCount = 0;
        let countStmt = null;
        try {
          countStmt = this._db.prepare(`SELECT COUNT(*) FROM ${safeIdent};`);
          if (countStmt.step()) {
            rowCount = Number(countStmt.get()[0] ?? 0);
          }
        } catch {
          rowCount = 0;
        } finally {
          countStmt?.free?.();
        }

        tables.push({
          name: entry.name,
          kind: entry.type,
          rowCount,
          columns,
        });
      }
    } finally {
      listStmt?.free?.();
    }
    return tables;
  }

  close() {
    try {
      this._db?.close();
    } finally {
      this._db = null;
    }
  }
}
