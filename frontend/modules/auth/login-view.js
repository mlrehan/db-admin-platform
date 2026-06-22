// Login view. Authenticates via AuthService, then routes to the app home.

import { app } from "../../core/context.js";
import { config } from "../../core/config.js";
import { bus, Events } from "../../core/events.js";

export class LoginView extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div class="login-screen">
        <form class="login-card" novalidate>
          <div class="login-logo"><span class="dot"></span>${config.appName}</div>
          <div class="login-sub">Sign in to manage your databases</div>
          <div class="login-error hidden" id="err"></div>
          <div class="field">
            <label for="email">Email</label>
            <input class="input" id="email" type="email" autocomplete="username"
              placeholder="you@example.com" required />
          </div>
          <div class="field">
            <label for="password">Password</label>
            <input class="input" id="password" type="password"
              autocomplete="current-password" placeholder="••••••••" required />
          </div>
          <button class="btn btn-primary" id="submit" style="width:100%">Sign in</button>
        </form>
      </div>`;

    this._form = this.querySelector("form");
    this._err = this.querySelector("#err");
    this._submit = this.querySelector("#submit");
    this._form.addEventListener("submit", (e) => this._onSubmit(e));
  }

  async _onSubmit(event) {
    event.preventDefault();
    const email = this.querySelector("#email").value.trim();
    const password = this.querySelector("#password").value;
    if (!email || !password) return;

    this._err.classList.add("hidden");
    this._submit.disabled = true;
    this._submit.textContent = "Signing in…";
    try {
      await app.auth.login(email, password);
      bus.emit(Events.TOAST, { message: "Welcome back", kind: "success" });
      app.router.navigate("/");
    } catch (err) {
      this._err.textContent =
        err?.code === "AUTHENTICATION_ERROR"
          ? "Invalid email or password."
          : err?.message || "Sign-in failed.";
      this._err.classList.remove("hidden");
    } finally {
      this._submit.disabled = false;
      this._submit.textContent = "Sign in";
    }
  }
}

customElements.define("login-view", LoginView);
