// Lazy loader for TomSelect (searchable select). Loaded from config.tomSelectBase — the CDN
// in dev, the vendored copy in the production image. Resolves to the TomSelect constructor, or
// null if it can't load, so callers degrade gracefully to a plain <select>.

import { config } from "./config.js";

let _promise = null;

export function loadTomSelect() {
  if (typeof window === "undefined") return Promise.resolve(null);
  if (window.TomSelect) return Promise.resolve(window.TomSelect);
  if (_promise) return _promise;
  _promise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${config.tomSelectBase}/css/tom-select.css`;
    document.head.appendChild(css);
    const s = document.createElement("script");
    s.src = `${config.tomSelectBase}/js/tom-select.complete.min.js`;
    s.onload = () =>
      window.TomSelect ? resolve(window.TomSelect) : reject(new Error("TomSelect missing"));
    s.onerror = () => reject(new Error("tom-select load failed"));
    document.head.appendChild(s);
    setTimeout(() => reject(new Error("tom-select timeout")), 8000);
  }).catch(() => null);
  return _promise;
}
