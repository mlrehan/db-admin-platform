// SQL helpers shared by the Table Viewer (server-side pagination/sorting). Identifier quoting
// is engine-specific; values are never interpolated here (the viewer only builds identifier-
// and integer-based SQL — user data goes through parameterized query execution elsewhere).

const QUOTES = {
  postgresql: ['"', '"'],
  mysql: ["`", "`"],
  mssql: ["[", "]"],
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
}) {
  const lim = clampInt(limit, 100);
  const off = clampInt(offset, 0);
  const dir = String(direction).toUpperCase() === "DESC" ? "DESC" : "ASC";
  let sql = `SELECT * FROM ${qualifiedName(engine, schema, table)}`;
  if (orderBy) sql += ` ORDER BY ${quoteIdent(engine, orderBy)} ${dir}`;

  if (engine === "mssql") {
    // OFFSET/FETCH requires an ORDER BY.
    if (!orderBy) sql += ` ORDER BY (SELECT NULL)`;
    sql += ` OFFSET ${off} ROWS FETCH NEXT ${lim} ROWS ONLY`;
  } else {
    sql += ` LIMIT ${lim} OFFSET ${off}`;
  }
  return sql;
}
