// Small helpers shared by views.

export function placeholderView(title, subtitle, phaseNote = "Arriving in Phase 8") {
  return `
    <div class="view">
      <div class="view-header">
        <h2>${title}</h2>
        <div class="muted">${subtitle}</div>
      </div>
      <div class="panel">
        <div class="placeholder">
          <div class="ph-icon">⌁</div>
          <div>${phaseNote}</div>
        </div>
      </div>
    </div>`;
}

// Define a Web Component whose innerHTML is produced by `render()` on connect.
export function defineView(tag, render) {
  if (customElements.get(tag)) return;
  customElements.define(
    tag,
    class extends HTMLElement {
      connectedCallback() {
        const out = render(this);
        if (typeof out === "string") this.innerHTML = out;
      }
    }
  );
}

export function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
