// Client-side cache of the IntelliSense catalog (GET /sessions/{id}/catalog), one per
// session + database. Typing never waits on the network after the first load:
//  • the catalog is fetched once and pre-indexed (buildIndex) for O(1) lookups,
//  • concurrent requests share one in-flight fetch,
//  • after DDL (Events.METADATA_CHANGED) entries are marked stale — the stale index keeps
//    serving suggestions while a fresh copy loads in the background,
//  • entries also expire after a TTL so changes made outside the platform show up.

import { app } from "./context.js";
import { bus, Events } from "./events.js";
import { buildIndex } from "./sql-intellisense.js";

const TTL_MS = 5 * 60 * 1000;
const store = new Map(); // key -> { promise, index, loadedAt, stale }

export function catalogKey(sessionId, database) {
  return `${sessionId}|${String(database || "").toLowerCase()}`;
}

/** The ready index for a session/database, or null (never blocks). */
export function peekIndex(sessionId, database) {
  return store.get(catalogKey(sessionId, database))?.index || null;
}

/** Load (or reuse) the catalog. Resolves to the built index. */
export function loadCatalog(sessionId, database, { force = false } = {}) {
  if (!sessionId || !app.api?.getCatalog) return Promise.resolve(null);
  const key = catalogKey(sessionId, database);
  const entry = store.get(key);
  const fresh = entry?.index && !entry.stale && Date.now() - entry.loadedAt < TTL_MS;
  if (!force && entry && (fresh || entry.loading)) return entry.promise;

  const next = { index: entry?.index || null, loadedAt: entry?.loadedAt || 0, stale: false, loading: true };
  next.promise = app.api
    .getCatalog(sessionId)
    .then((raw) => {
      next.index = buildIndex(raw);
      next.loadedAt = Date.now();
      next.loading = false;
      return next.index;
    })
    .catch((err) => {
      next.loading = false;
      if (!next.index) store.delete(key);
      throw err;
    });
  next.promise.catch(() => {}); // a failed prefetch must not surface as an unhandled rejection
  store.set(key, next);
  return next.promise;
}

/** Mark every cached catalog stale (kept usable until the reload finishes). */
export function invalidateCatalogs() {
  for (const entry of store.values()) entry.stale = true;
}

export function clearCatalogs() {
  store.clear();
}

bus.on(Events.METADATA_CHANGED, invalidateCatalogs);
