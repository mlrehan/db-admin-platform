// WebSocket client for streaming query execution. Wraps the backend protocol
// (accepted/columns/rows/end/error/cancelled) behind an event-style API. The WebSocket
// implementation is injectable so the message-handling logic is unit-testable in Node.

import { resolveWsUrl } from "../core/config.js";

export class QueryStream {
  /**
   * @param {object} opts
   * @param {string} opts.sessionId
   * @param {()=>string|null} opts.getAccessToken
   * @param {typeof WebSocket} [opts.WebSocketImpl]
   * @param {(url:string)=>string} [opts.urlResolver]
   */
  constructor({ sessionId, getAccessToken, WebSocketImpl, urlResolver }) {
    this.sessionId = sessionId;
    this.getAccessToken = getAccessToken;
    this.WebSocketImpl =
      WebSocketImpl || (typeof WebSocket !== "undefined" ? WebSocket : null);
    this.urlResolver = urlResolver || resolveWsUrl;
    this.ws = null;
    this.handlers = {
      accepted: [],
      columns: [],
      rows: [],
      end: [],
      error: [],
      cancelled: [],
      open: [],
      close: [],
    };
  }

  on(type, handler) {
    if (!this.handlers[type]) this.handlers[type] = [];
    this.handlers[type].push(handler);
    return this;
  }

  _emit(type, payload) {
    for (const h of this.handlers[type] ?? []) h(payload);
  }

  connect() {
    const token = this.getAccessToken?.() || "";
    const url = this.urlResolver(
      `/ws/sessions/${this.sessionId}/query?token=${encodeURIComponent(token)}`
    );
    this.ws = new this.WebSocketImpl(url);
    this.ws.onopen = () => this._emit("open");
    this.ws.onclose = (e) => this._emit("close", e);
    this.ws.onmessage = (event) => this._dispatch(event.data);
    this.ws.onerror = () =>
      this._emit("error", { code: "WS_ERROR", message: "WebSocket error" });
    return this;
  }

  _dispatch(raw) {
    let msg;
    try {
      msg = typeof raw === "string" ? JSON.parse(raw) : raw;
    } catch {
      this._emit("error", { code: "BAD_MESSAGE", message: "Malformed server message" });
      return;
    }
    this._emit(msg.type, msg);
  }

  execute(sql, { params = null, batchSize } = {}) {
    this._send({ action: "execute", sql, params, batch_size: batchSize });
  }

  cancel() {
    this._send({ action: "cancel" });
  }

  ping() {
    this._send({ action: "ping" });
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  close() {
    this.ws?.close();
    this.ws = null;
  }
}
