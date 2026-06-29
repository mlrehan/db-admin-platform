// Inactivity (idle) timeout. Signs the user out after a period of NO activity, with a clear
// warning shortly before. This is purely client-side: the access token is transparently
// refreshed while the user is active, so they are never logged out mid-use — only when they
// explicitly log out, when this idle timer fires, or when their token is irrecoverably invalid.

import { app } from "./context.js";
import { bus, Events } from "./events.js";
import { confirm, closeDialog } from "./notify.js";

const IDLE_MS = 2 * 60 * 60 * 1000; // 2 hours of inactivity → sign out
const WARN_MS = 2 * 60 * 1000; // show the final warning 2 minutes before
const ACTIVITY_EVENTS = ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "click"];

let active = false;
let idleId = null;
let warnId = null;
let warnOpen = false;
let onActivity = null;

function clearTimers() {
  clearTimeout(idleId);
  clearTimeout(warnId);
  idleId = warnId = null;
}

function arm() {
  clearTimers();
  if (!active) return;
  warnId = setTimeout(warn, Math.max(IDLE_MS - WARN_MS, 0));
  idleId = setTimeout(expire, IDLE_MS);
}

// Reset the idle window on activity (throttled), unless the warning dialog is open — then the
// user must explicitly choose to stay signed in.
function bump() {
  if (active && !warnOpen) arm();
}

async function warn() {
  if (!active) return;
  warnOpen = true;
  const stay = await confirm({
    title: "Are you still there?",
    text: "For your security, you'll be signed out in 2 minutes due to inactivity.",
    confirmText: "Stay signed in",
  });
  warnOpen = false;
  if (active && stay) arm(); // chose to stay → restart the full idle window
}

async function expire() {
  if (!active) return;
  active = false;
  clearTimers();
  closeDialog(); // dismiss the warning dialog if it's still up
  bus.emit(Events.TOAST, {
    message: "You were signed out after 2 hours of inactivity.",
    kind: "info",
  });
  try {
    await app.auth?.logout();
  } finally {
    app.router?.navigate("/login");
  }
}

export function startIdleTimer() {
  if (active) return;
  active = true;
  onActivity = throttle(bump, 5000);
  ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity, { passive: true }));
  arm();
}

export function stopIdleTimer() {
  active = false;
  warnOpen = false;
  clearTimers();
  if (onActivity) ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity));
  onActivity = null;
}

function throttle(fn, ms) {
  let last = 0;
  return () => {
    const now = Date.now();
    if (now - last >= ms) {
      last = now;
      fn();
    }
  };
}
