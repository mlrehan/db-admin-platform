// Lightweight modal helper. Appends an overlay to <body>, traps Escape, returns a close fn.
// The modal deliberately does NOT close on outside (backdrop) click — to avoid losing a
// half-filled form by accident. Close with Esc or the ✕ button.

export function openModal({ title, content, width = 460 }) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal" style="width:${width}px" role="dialog" aria-modal="true">
      <div class="modal-head">
        <h3>${title}</h3>
        <button class="btn btn-ghost modal-close" aria-label="Close">✕</button>
      </div>
      <div class="modal-body"></div>
    </div>`;
  overlay.querySelector(".modal-body").appendChild(content);
  document.body.appendChild(overlay);

  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  }
  function onKey(e) {
    if (e.key === "Escape") close();
  }
  // Intentionally no backdrop-click-to-close: only Esc or the ✕ button dismiss the modal.
  overlay.querySelector(".modal-close").addEventListener("click", close);
  document.addEventListener("keydown", onKey);
  return close;
}
