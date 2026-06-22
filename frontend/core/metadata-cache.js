// Small TTL cache for read-only metadata responses (databases/schemas/tables/…). It dedupes
// repeated fetches as the user moves between the Schema Explorer, Data Viewer and session
// bar. The cache is cleared whenever the active session or database changes (see
// session-state.js), and entries expire after a short TTL, so it never serves stale schema
// across a context switch.

const _store = new Map();
const DEFAULT_TTL = 30_000;

export function cached(key, fetcher, ttl = DEFAULT_TTL) {
  const hit = _store.get(key);
  if (hit && hit.expires > Date.now()) return hit.value;
  // Store the promise so concurrent callers share one in-flight request.
  const value = Promise.resolve().then(fetcher);
  _store.set(key, { value, expires: Date.now() + ttl });
  value.catch(() => _store.delete(key)); // don't cache failures
  return value;
}

export function clearMetadataCache() {
  _store.clear();
}
