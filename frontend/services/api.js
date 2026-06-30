// High-level API surface. One small method per backend endpoint so views never build URLs
// by hand. Grouped by subsystem to mirror the backend routers.

import { cached } from "../core/metadata-cache.js";

export class Api {
  constructor(http) {
    this.http = http;
  }

  // --- connections ---
  listConnections({ allOwners = false } = {}) {
    // allOwners (admin only) lists across every owner — used to resolve connection names for
    // the audit log; non-admins simply get their own + shared connections.
    return this.http.get(`/connections${allOwners ? "?all_owners=true" : ""}`);
  }
  createConnection(payload) {
    return this.http.post("/connections", payload);
  }
  getConnection(id) {
    return this.http.get(`/connections/${id}`);
  }
  updateConnection(id, payload) {
    return this.http.patch(`/connections/${id}`, payload);
  }
  deleteConnection(id) {
    return this.http.delete(`/connections/${id}`);
  }
  testConnection(id) {
    return this.http.post(`/connections/${id}/test`);
  }
  // Introspection for the access-grant picker (admin/owner only).
  listConnectionDatabases(id) {
    return this.http.get(`/connections/${id}/databases`);
  }
  listConnectionTables(id, database) {
    const q = database ? `?database=${encodeURIComponent(database)}` : "";
    return this.http.get(`/connections/${id}/tables${q}`);
  }

  // --- live sessions ---
  openSession(connectionId) {
    return this.http.post("/sessions", { connection_id: connectionId });
  }
  listSessions() {
    return this.http.get("/sessions");
  }
  closeSession(id) {
    return this.http.delete(`/sessions/${id}`);
  }

  // --- query (buffered) ---
  executeQuery(sessionId, sql, params, maxRows) {
    return this.http.post(`/sessions/${sessionId}/query`, {
      sql,
      params: params ?? null,
      max_rows: maxRows ?? null,
    });
  }
  executeScript(sessionId, sql, maxRows) {
    return this.http.post(`/sessions/${sessionId}/script`, {
      sql,
      max_rows: maxRows ?? null,
    });
  }
  listRunningQueries() {
    return this.http.get("/queries/running");
  }
  cancelQuery(queryId) {
    return this.http.post(`/queries/${queryId}/cancel`);
  }

  // --- databases (server-level connections) ---
  listDatabases(sessionId) {
    return cached(`db:${sessionId}`, () =>
      this.http.get(`/sessions/${sessionId}/databases`)
    );
  }
  switchDatabase(sessionId, database) {
    // Not cached (mutates session state); cache is cleared on the active-database change.
    return this.http.post(`/sessions/${sessionId}/database`, { database });
  }
  createDatabase(sessionId, name) {
    return this.http.post(`/sessions/${sessionId}/databases`, { name });
  }

  // --- schema introspection (cached; invalidated on session/database change) ---
  listSchemas(sessionId) {
    return cached(`schemas:${sessionId}`, () =>
      this.http.get(`/sessions/${sessionId}/schemas`)
    );
  }
  listTables(sessionId, schema) {
    const q = schema ? `?schema=${encodeURIComponent(schema)}` : "";
    return cached(`tables:${sessionId}:${schema || ""}`, () =>
      this.http.get(`/sessions/${sessionId}/tables${q}`)
    );
  }
  describeTable(sessionId, table, schema) {
    const q = schema ? `?schema=${encodeURIComponent(schema)}` : "";
    return cached(`table:${sessionId}:${schema || ""}:${table}`, () =>
      this.http.get(`/sessions/${sessionId}/tables/${encodeURIComponent(table)}${q}`)
    );
  }
  listRoutines(sessionId, schema) {
    const q = schema ? `?schema=${encodeURIComponent(schema)}` : "";
    return cached(`routines:${sessionId}:${schema || ""}`, () =>
      this.http.get(`/sessions/${sessionId}/routines${q}`)
    );
  }

  // --- admin: users ---
  listUsers() {
    return this.http.get("/users");
  }
  createUser(payload) {
    return this.http.post("/users", payload);
  }
  updateUser(id, payload) {
    return this.http.patch(`/users/${id}`, payload);
  }
  deleteUser(id) {
    return this.http.delete(`/users/${id}`);
  }

  // --- access grants (granular RBAC) ---
  listGrantableOperations() {
    return this.http.get("/access/operations");
  }
  listGrants(connectionId) {
    const q = connectionId ? `?connection_id=${connectionId}` : "";
    return this.http.get(`/access/grants${q}`);
  }
  createGrant(payload) {
    return this.http.post("/access/grants", payload);
  }
  updateGrant(id, payload) {
    return this.http.patch(`/access/grants/${id}`, payload);
  }
  deleteGrant(id) {
    return this.http.delete(`/access/grants/${id}`);
  }

  // --- audit ---
  listAuditLogs(filters = {}) {
    const q = new URLSearchParams(
      Object.entries(filters).filter(([, v]) => v !== undefined && v !== null)
    ).toString();
    return this.http.get(`/audit/logs${q ? `?${q}` : ""}`);
  }
}
