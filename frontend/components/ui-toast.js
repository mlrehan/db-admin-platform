// Bridges the app's TOAST event bus to SweetAlert2 toasts. Kept as a custom element so the
// existing `bus.emit(Events.TOAST, …)` call sites keep working unchanged.

import { bus, Events } from "../core/events.js";
import { toast } from "../core/notify.js";

export class ToastHost extends HTMLElement {
  connectedCallback() {
    this._unsub = bus.on(Events.TOAST, ({ message, kind = "info" }) => {
      const icon = ["success", "error", "warning", "info"].includes(kind) ? kind : "info";
      toast(message, icon);
    });
  }

  disconnectedCallback() {
    this._unsub?.();
  }
}

customElements.define("ui-toast", ToastHost);
