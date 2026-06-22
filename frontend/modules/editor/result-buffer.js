// Accumulates streaming query events (accepted/columns/rows/end/error/cancelled) into a
// renderable result state. Pure and framework-free so the streaming logic is unit-testable.

export class ResultBuffer {
  constructor() {
    this.reset();
  }

  reset() {
    this.status = "idle"; // idle | running | done | error | cancelled
    this.queryId = null;
    this.columns = [];
    this.rows = [];
    this.rowCount = 0;
    this.rowsAffected = null;
    this.returnsRows = true;
    this.category = null;
    this.destructive = false;
    this.error = null;
    return this;
  }

  handle(evt) {
    switch (evt.type) {
      case "accepted":
        this.status = "running";
        this.queryId = evt.query_id ?? null;
        this.category = evt.category ?? this.category;
        break;
      case "columns":
        this.columns = evt.columns ?? [];
        if (evt.returns_rows !== undefined) this.returnsRows = evt.returns_rows;
        break;
      case "rows":
        if (evt.rows && evt.rows.length) {
          this.rows.push(...evt.rows);
          this.rowCount = this.rows.length;
        }
        break;
      case "end":
        this.status = "done";
        if (evt.rows_affected !== undefined) this.rowsAffected = evt.rows_affected;
        if (evt.category != null) this.category = evt.category;
        if (evt.destructive != null) this.destructive = evt.destructive;
        if (evt.row_count != null) this.rowCount = evt.row_count;
        break;
      case "error":
        this.status = "error";
        this.error = evt.message || evt.code || "Query failed";
        break;
      case "cancelled":
        this.status = "cancelled";
        break;
      default:
        break;
    }
    return this;
  }

  get isTerminal() {
    return ["done", "error", "cancelled"].includes(this.status);
  }
}
