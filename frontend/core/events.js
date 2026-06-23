// Minimal typed publish/subscribe event bus. Used for cross-component signals
// (auth state changes, toasts) without a heavy framework.

export class EventBus {
  constructor() {
    this._listeners = new Map();
  }

  on(event, handler) {
    if (!this._listeners.has(event)) this._listeners.set(event, new Set());
    this._listeners.get(event).add(handler);
    return () => this.off(event, handler);
  }

  off(event, handler) {
    this._listeners.get(event)?.delete(handler);
  }

  emit(event, payload) {
    for (const handler of this._listeners.get(event) ?? []) {
      try {
        handler(payload);
      } catch (err) {
        // A misbehaving listener must not break the emit loop.
        console.error(`Event handler for "${event}" failed`, err);
      }
    }
  }
}

// App-wide singleton bus.
export const bus = new EventBus();

export const Events = Object.freeze({
  AUTH_CHANGED: "auth:changed",
  UNAUTHORIZED: "auth:unauthorized",
  TOAST: "ui:toast",
  // Emitted after DDL runs so metadata views (Schema Explorer, etc.) reload.
  METADATA_CHANGED: "metadata:changed",
});
