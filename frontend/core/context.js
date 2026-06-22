// App context: a single object wiring the service singletons, populated once by main.js and
// imported by components. Avoids a heavyweight DI container while keeping components free of
// construction logic.

export const app = {
  /** @type {import("../services/auth.js").AuthService|null} */
  auth: null,
  /** @type {import("../services/api.js").Api|null} */
  api: null,
  /** @type {import("./router.js").Router|null} */
  router: null,
  /** @type {import("../services/http.js").HttpClient|null} */
  http: null,
};
