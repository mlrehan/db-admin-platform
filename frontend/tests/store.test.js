import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../core/store.js";
import { EventBus } from "../core/events.js";

test("store notifies subscribers on change", () => {
  const store = createStore({ count: 0 });
  const seen = [];
  const unsub = store.subscribe((s) => seen.push(s.count));
  store.setState({ count: 1 });
  store.setState((s) => ({ count: s.count + 1 }));
  assert.deepEqual(seen, [1, 2]);
  assert.equal(store.getState().count, 2);
  unsub();
  store.setState({ count: 99 });
  assert.deepEqual(seen, [1, 2]); // no further notifications after unsubscribe
});

test("event bus on/emit/off", () => {
  const bus = new EventBus();
  let got = null;
  const off = bus.on("x", (p) => (got = p));
  bus.emit("x", 42);
  assert.equal(got, 42);
  off();
  bus.emit("x", 7);
  assert.equal(got, 42); // unsubscribed
});

test("event bus isolates listener errors", () => {
  const bus = new EventBus();
  let reached = false;
  bus.on("e", () => {
    throw new Error("boom");
  });
  bus.on("e", () => (reached = true));
  bus.emit("e");
  assert.equal(reached, true);
});
