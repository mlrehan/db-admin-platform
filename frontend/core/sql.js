// SQL helpers shared by the Table Viewer (server-side pagination/sorting). Identifier quoting
// is engine-specific; values are never interpolated here (the viewer only builds identifier-
// and integer-based SQL — user data goes through parameterized query execution elsewhere).

const QUOTES = {
  postgresql: ['"', '"'],
  mysql: ["`", "`"],
  mssql: ["[", "]"],
  sqlite: ['"', '"'],
};

export function quoteIdent(engine, name) {
  const [open, close] = QUOTES[engine] || ['"', '"'];
  const text = String(name);
  if (engine === "mssql") return open + text.replace(/]/g, "]]") + close;
  // Same opening/closing char (" or `): double it to escape.
  return open + text.split(open).join(open + open) + close;
}

export function qualifiedName(engine, schema, table) {
  const t = quoteIdent(engine, table);
  return schema ? `${quoteIdent(engine, schema)}.${t}` : t;
}

function clampInt(value, fallback) {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

export function buildSelect({
  engine,
  schema,
  table,
  limit = 100,
  offset = 0,
  orderBy = null,
  direction = "asc",
  keyColumns = [],
}) {
  const lim = clampInt(limit, 100);
  const off = clampInt(offset, 0);
  const dir = String(direction).toUpperCase() === "DESC" ? "DESC" : "ASC";
  let sql = `SELECT * FROM ${qualifiedName(engine, schema, table)}`;
  // Paging with LIMIT/OFFSET is only correct over a TOTAL order: without one the database may
  // return rows in a different order per query, so pages overlap or skip rows. The primary key
  // (keyColumns) makes the order deterministic — alone, or as a tiebreaker after the user's
  // chosen (possibly non-unique) ordering column.
  const order = [];
  if (orderBy) order.push(`${quoteIdent(engine, orderBy)} ${dir}`);
  for (const key of keyColumns || []) {
    if (key && key !== orderBy) order.push(`${quoteIdent(engine, key)} ASC`);
  }
  if (order.length) sql += ` ORDER BY ${order.join(", ")}`;

  if (engine === "mssql") {
    // OFFSET/FETCH requires an ORDER BY (tables without a primary key, no ordering chosen).
    if (!order.length) sql += ` ORDER BY (SELECT NULL)`;
    sql += ` OFFSET ${off} ROWS FETCH NEXT ${lim} ROWS ONLY`;
  } else {
    sql += ` LIMIT ${lim} OFFSET ${off}`;
  }
  return sql;
}

// Total row count for the viewer's pager ("Rows 101–200 of 1,234").
export function buildCount({ engine, schema, table }) {
  return `SELECT COUNT(*) AS total_rows FROM ${qualifiedName(engine, schema, table)}`;
}
