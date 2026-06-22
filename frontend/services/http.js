// HTTP client: a thin fetch wrapper that attaches the bearer token, parses the backend's
// error envelope, and transparently refreshes an expired access token (single-flight) before
// retrying the original request once. Dependencies are injected for testability.

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message || code || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class HttpClient {
  /**
   * @param {object} opts
   * @param {string} opts.baseUrl
   * @param {()=>string|null} opts.getAccessToken
   * @param {()=>Promise<boolean>} opts.refresh  Attempts a token refresh; resolves to success.
   * @param {()=>void} [opts.onUnauthorized]     Called when refresh ultimately fails.
   * @param {typeof fetch} [opts.fetch]
   */
  constructor({ baseUrl, getAccessToken, refresh, onUnauthorized, fetch: fetchImpl }) {
    this.baseUrl = baseUrl;
    this.getAccessToken = getAccessToken;
    this.refresh = refresh;
    this.onUnauthorized = onUnauthorized || (() => {});
    this.fetch = fetchImpl || (typeof fetch !== "undefined" ? fetch.bind(globalThis) : null);
    this._refreshing = null; // single-flight refresh promise
  }

  get(path, opts) {
    return this.request("GET", path, undefined, opts);
  }
  post(path, body, opts) {
    return this.request("POST", path, body, opts);
  }
  patch(path, body, opts) {
    return this.request("PATCH", path, body, opts);
  }
  put(path, body, opts) {
    return this.request("PUT", path, body, opts);
  }
  delete(path, opts) {
    return this.request("DELETE", path, undefined, opts);
  }

  async request(method, path, body, { retry = true, auth = true } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const token = auth ? this.getAccessToken?.() : null;
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const response = await this.fetch(this.baseUrl + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    // Transparent refresh-and-retry on a single 401.
    if (response.status === 401 && auth && retry && this.refresh) {
      const refreshed = await this._refreshOnce();
      if (refreshed) {
        return this.request(method, path, body, { retry: false, auth });
      }
      this.onUnauthorized();
      throw await toApiError(response);
    }

    if (response.status === 204) return null;
    if (!response.ok) throw await toApiError(response);

    const text = await response.text();
    return text ? JSON.parse(text) : null;
  }

  _refreshOnce() {
    if (!this._refreshing) {
      this._refreshing = Promise.resolve()
        .then(() => this.refresh())
        .finally(() => {
          this._refreshing = null;
        });
    }
    return this._refreshing;
  }
}

async function toApiError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body */
  }
  const error = payload?.error;
  return new ApiError(
    response.status,
    error?.code,
    error?.message || response.statusText,
    error?.details
  );
}
