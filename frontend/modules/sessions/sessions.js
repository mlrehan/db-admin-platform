// Active sessions manager. A "session" is a live connection your account has open to a
// database server (created when you click Connect). This dialog lists them and lets you
// disconnect — closing the underlying connection and freeing server resources.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { openModal } from "../../components/modal.js";
import { confirm } from "../../core/notify.js";
import { escapeHtml } from "../../components/view-helpers.js";
import { sessionStore, setActiveSession } from "../../core/session-state.js";

export async function openSessions() {
  const body = document.createElement("div");
  body.innerHTML = `<div class="muted" style="padding:8px 0">Loading sessions…</div>`;
  const close = openModal({ title: "Active database sessions", content: body, width: 560 });

  async function render() {
    let sessions = [];
    try {
      sessions = await app.api.listSessions();
    } catch (err) {
      body.innerHTML = `<div class="login-error">${escapeHtml(err.message)}</div>`;
      return;
    }
    if (!sessions.length) {
      body.innerHTML = `
        <p class="muted">You have no open sessions.</p>
        <p class="muted" style="font-size:var(--fs-sm)">A <strong>session</strong> is a live
        connection to a database server. Open one from <strong>Connections → Connect</strong>.</p>`;
      return;
    }
    body.innerHTML = `
      <p class="muted" style="font-size:var(--fs-sm); margin-top:0">
        A <strong>session</strong> is a live connection to a database server. Disconnect to close it.</p>
      <table class="grid-table">
        <thead><tr><th>Engine</th><th>Database</th><th>Idle</th><th></th></tr></thead>
        <tbody>${sessions
          .map(
            (s) => `<tr>
              <td><span class="badge">${escapeHtml(s.engine)}</span></td>
              <td class="mono">${escapeHtml(s.active_database || "—")}</td>
              <td class="muted">${Math.round(s.idle_seconds)}s</td>
              <td style="text-align:right">
                <button class="btn btn-ghost btn-danger" data-close="${s.id}">Disconnect</button>
              </td></tr>`
          )
          .join("")}</tbody>
      </table>`;

    body.querySelectorAll("[data-close]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const ok = await confirm({
          title: "Disconnect session?",
          text: "This closes the live connection to the database server.",
          confirmText: "Disconnect",
          danger: true,
        });
        if (!ok) return;
        try {
          const id = btn.dataset.close;
          await app.api.closeSession(id);
          if (sessionStore.getState().sessionId === id) setActiveSession(null);
          bus.emit(Events.TOAST, { message: "Session disconnected", kind: "success" });
          render();
        } catch (err) {
          bus.emit(Events.TOAST, { message: err.message, kind: "error" });
        }
      })
    );
  }

  await render();
  return close;
}
