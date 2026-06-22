import { test } from "node:test";
import assert from "node:assert/strict";
import { QueryStream } from "../services/ws.js";

// Fake WebSocket capturing sends and exposing hooks to simulate server messages.
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.sent = [];
    FakeWebSocket.last = this;
  }
  send(data) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
    this.onclose?.({});
  }
  _server(msg) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

function makeStream() {
  return new QueryStream({
    sessionId: "s1",
    getAccessToken: () => "tok",
    WebSocketImpl: FakeWebSocket,
    urlResolver: (p) => "ws://test" + p,
  });
}

test("connect builds an authenticated URL", () => {
  const stream = makeStream().connect();
  assert.match(FakeWebSocket.last.url, /\/ws\/sessions\/s1\/query\?token=tok$/);
});

test("dispatches typed events from server messages", () => {
  const stream = makeStream().connect();
  const events = [];
  stream.on("columns", (m) => events.push(["columns", m.columns]));
  stream.on("rows", (m) => events.push(["rows", m.rows]));
  stream.on("end", (m) => events.push(["end", m.row_count]));

  FakeWebSocket.last._server({ type: "columns", columns: [{ name: "id" }] });
  FakeWebSocket.last._server({ type: "rows", rows: [[1]] });
  FakeWebSocket.last._server({ type: "end", row_count: 1 });

  assert.deepEqual(events, [
    ["columns", [{ name: "id" }]],
    ["rows", [[1]]],
    ["end", 1],
  ]);
});

test("execute and cancel send the right frames", () => {
  const stream = makeStream().connect();
  stream.execute("SELECT 1", { batchSize: 100 });
  stream.cancel();
  assert.deepEqual(FakeWebSocket.last.sent, [
    { action: "execute", sql: "SELECT 1", params: null, batch_size: 100 },
    { action: "cancel" },
  ]);
});

test("malformed server message surfaces an error event", () => {
  const stream = makeStream().connect();
  let err = null;
  stream.on("error", (m) => (err = m));
  FakeWebSocket.last.onmessage?.({ data: "{not json" });
  assert.equal(err.code, "BAD_MESSAGE");
});
