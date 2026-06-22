// Change-password dialog. On success the backend revokes all sessions, so we sign the user
// out and return them to the login screen to re-authenticate with the new password.

import { app } from "../../core/context.js";
import { bus, Events } from "../../core/events.js";
import { openModal } from "../../components/modal.js";

export function openChangePassword() {
  const form = document.createElement("form");
  form.className = "modal-form";
  form.innerHTML = `
    <div class="login-error hidden" id="cp-err"></div>
    <div class="field"><label>Current password</label>
      <input class="input" name="current" type="password" autocomplete="current-password" required></div>
    <div class="field"><label>New password <span class="muted">(min 12 characters)</span></label>
      <input class="input" name="next" type="password" autocomplete="new-password" minlength="12" required></div>
    <div class="field"><label>Confirm new password</label>
      <input class="input" name="confirm" type="password" autocomplete="new-password" required></div>
    <div class="row" style="justify-content:flex-end">
      <button class="btn btn-primary" id="cp-submit">Change password</button>
    </div>`;

  const err = form.querySelector("#cp-err");
  const showError = (msg) => {
    err.textContent = msg;
    err.classList.remove("hidden");
  };

  const close = openModal({ title: "Change password", content: form, width: 420 });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(form).entries());
    if (d.next !== d.confirm) return showError("New passwords do not match.");
    if (d.next.length < 12) return showError("New password must be at least 12 characters.");

    const btn = form.querySelector("#cp-submit");
    btn.disabled = true;
    btn.textContent = "Changing…";
    try {
      await app.auth.changePassword(d.current, d.next);
      close();
      bus.emit(Events.TOAST, {
        message: "Password changed — please sign in again.",
        kind: "success",
      });
      bus.emit(Events.AUTH_CHANGED, { authenticated: false });
      app.router.navigate("/login");
    } catch (ex) {
      showError(
        ex?.code === "AUTHENTICATION_ERROR"
          ? "Current password is incorrect."
          : ex?.message || "Could not change password."
      );
      btn.disabled = false;
      btn.textContent = "Change password";
    }
  });
}
