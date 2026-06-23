// Unified notifications/dialogs built on SweetAlert2 (loaded lazily from config.swalBase,
// vendored in the production image). Every helper degrades gracefully to native browser
// dialogs if SweetAlert2 cannot load, so the app never breaks offline.

import { config } from "./config.js";
import { getTheme } from "./theme.js";

let _swalPromise = null;

function loadSwal() {
  if (window.Swal) return Promise.resolve(window.Swal);
  if (_swalPromise) return _swalPromise;
  _swalPromise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${config.swalBase}/dist/sweetalert2.min.css`;
    document.head.appendChild(css);
    const s = document.createElement("script");
    s.src = `${config.swalBase}/dist/sweetalert2.min.js`;
    s.onload = () => (window.Swal ? resolve(window.Swal) : reject(new Error("no Swal")));
    s.onerror = () => reject(new Error("swal load failed"));
    document.head.appendChild(s);
    setTimeout(() => reject(new Error("swal timeout")), 8000);
  }).catch(() => null);
  return _swalPromise;
}

// Preload so dialogs are instant when first used.
export function initNotify() {
  loadSwal();
}

function themed(extra = {}) {
  const dark = getTheme() === "dark";
  return {
    background: dark ? "#18212c" : "#ffffff",
    color: dark ? "#e6edf3" : "#16212d",
    confirmButtonColor: "#1f9d8e",
    cancelButtonColor: dark ? "#33404e" : "#aab7c4",
    ...extra,
  };
}

export async function toast(message, kind = "info") {
  const Swal = await loadSwal();
  if (!Swal) return;
  Swal.fire({
    toast: true,
    position: "bottom-end",
    icon: kind === "info" ? "info" : kind,
    title: message,
    showConfirmButton: false,
    timer: kind === "error" ? 5000 : 3000,
    timerProgressBar: true,
    ...themed(),
  });
}

export async function alertError(message, title = "Something went wrong") {
  const Swal = await loadSwal();
  if (!Swal) return window.alert(`${title}\n\n${message}`);
  return Swal.fire({ icon: "error", title, text: message, ...themed() });
}

export async function confirm({
  title = "Are you sure?",
  text = "",
  confirmText = "Confirm",
  danger = false,
} = {}) {
  const Swal = await loadSwal();
  if (!Swal) return window.confirm(`${title}\n\n${text}`);
  const res = await Swal.fire({
    icon: danger ? "warning" : "question",
    title,
    text,
    showCancelButton: true,
    confirmButtonText: confirmText,
    cancelButtonText: "Cancel",
    reverseButtons: true,
    ...themed(danger ? { confirmButtonColor: "#cf222e" } : {}),
  });
  return res.isConfirmed;
}

// Ask the user for a single line of text. Resolves to the trimmed string, or null if
// cancelled. Falls back to the native prompt if SweetAlert2 is unavailable.
export async function promptText({
  title = "Enter a value",
  text = "",
  placeholder = "",
  confirmText = "OK",
  value = "",
} = {}) {
  const Swal = await loadSwal();
  if (!Swal) {
    const r = window.prompt(`${title}${text ? `\n${text}` : ""}`, value);
    return r == null ? null : r.trim();
  }
  const res = await Swal.fire({
    title,
    text,
    input: "text",
    inputValue: value,
    inputPlaceholder: placeholder,
    showCancelButton: true,
    confirmButtonText: confirmText,
    cancelButtonText: "Cancel",
    reverseButtons: true,
    inputValidator: (v) => (v && v.trim() ? undefined : "Please enter a value"),
    ...themed(),
  });
  return res.isConfirmed ? String(res.value).trim() : null;
}

// Show a blocking busy/loading dialog; returns a function to close it.
export async function loading(title = "Working…", text = "") {
  const Swal = await loadSwal();
  if (!Swal) return () => {};
  Swal.fire({
    title,
    text,
    allowOutsideClick: false,
    allowEscapeKey: false,
    didOpen: () => Swal.showLoading(),
    ...themed(),
  });
  return () => Swal.close();
}
