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
