// Authentication service: owns token lifecycle and the current-user state.
//
// Token storage strategy: the short-lived access token lives only in memory; the refresh
// token is persisted in localStorage so a page reload can re-establish a session. This is a
// pragmatic SPA trade-off — a hardened deployment would move the refresh token to an
// httpOnly cookie (requires the backend to set cookies). Documented intentionally.

import { config } from "../core/config.js";
import { bus, Events } from "../core/events.js";

export class AuthService {
  /**
   * @param {object} opts
   * @param {import("./http.js").HttpClient} opts.http
   * @param {Storage} [opts.storage]  Defaults to window.localStorage.
   */
  constructor({ http, storage }) {
    this.http = http;
    this.storage =
      storage || (typeof localStorage !== "undefined" ? localStorage : memoryStorage());
    this._accessToken = null;
    this._user = null;
  }

  getAccessToken() {
    return this._accessToken;
  }

  getRefreshToken() {
    return this.storage.getItem(config.refreshTokenKey);
  }

  get user() {
    return this._user;
  }

  isAuthenticated() {
    return Boolean(this._accessToken);
  }

  _setTokens(access, refresh) {
    this._accessToken = access || null;
    if (refresh) this.storage.setItem(config.refreshTokenKey, refresh);
  }

  async login(email, password) {
    const tokens = await this.http.post(
      "/auth/login",
      { email, password },
      { auth: false }
    );
    this._setTokens(tokens.access_token, tokens.refresh_token);
    await this.loadProfile();
    bus.emit(Events.AUTH_CHANGED, { authenticated: true, user: this._user });
    return this._user;
  }

  async loadProfile() {
    this._user = await this.http.get("/auth/me");
    return this._user;
  }

  // Used by HttpClient on a 401. Returns true on success.
  async refresh() {
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) return false;
    try {
      const tokens = await this.http.post(
        "/auth/refresh",
        { refresh_token: refreshToken },
        { auth: false }
      );
      this._setTokens(tokens.access_token, tokens.refresh_token);
      return true;
    } catch {
      this.clear();
      return false;
    }
  }

  // Re-establish a session on app load using a persisted refresh token.
  async restore() {
    if (!this.getRefreshToken()) return false;
    const ok = await this.refresh();
    if (ok) {
      try {
        await this.loadProfile();
        bus.emit(Events.AUTH_CHANGED, { authenticated: true, user: this._user });
        return true;
      } catch {
        this.clear();
      }
    }
    return false;
  }

  async changePassword(currentPassword, newPassword) {
    await this.http.post("/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword,
    });
    // The backend revokes all sessions on a password change; force re-authentication.
    this.clear();
  }

  async logout() {
    try {
      if (this._accessToken) await this.http.post("/auth/logout");
    } catch {
      /* best-effort */
    }
    this.clear();
    bus.emit(Events.AUTH_CHANGED, { authenticated: false, user: null });
  }

  clear() {
    this._accessToken = null;
    this._user = null;
    this.storage.removeItem(config.refreshTokenKey);
  }
}

function memoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
}
