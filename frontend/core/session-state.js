// Shared "active live session" state so the Editor, Schema Explorer and Data Viewer all
// operate on the same selected session.

import { createStore } from "./store.js";
import { clearMetadataCache } from "./metadata-cache.js";

export const sessionStore = createStore({
  sessionId: null,
  engine: null,
  label: null,
  database: null, // active database (server-level connections can switch)
});

export function setActiveSession(session) {
  clearMetadataCache(); // switching session invalidates cached schema/databases
  sessionStore.setState({
    sessionId: session?.id ?? null,
    engine: session?.engine ?? null,
    label: session ? `${session.engine} · ${session.id.slice(0, 8)}` : null,
    database: session?.active_database ?? null,
  });
}

export function setActiveDatabase(database) {
  clearMetadataCache(); // switching database invalidates cached schema/tables
  sessionStore.setState({ database: database || null });
}

// A one-shot hand-off of SQL to the editor across a route change (e.g. "Open in SQL Editor"
// from the Schema Explorer's object-definition viewer). The editor consumes it on mount.
let _pendingEditorSql = null;
export function setPendingEditorSql(sql) {
  _pendingEditorSql = sql;
}
export function takePendingEditorSql() {
  const sql = _pendingEditorSql;
  _pendingEditorSql = null;
  return sql;
}
