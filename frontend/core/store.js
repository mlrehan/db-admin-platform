// A tiny reactive store. Holds plain state, notifies subscribers on change.
// Deliberately dependency-free and environment-agnostic (testable in Node).

export function createStore(initialState = {}) {
  let state = { ...initialState };
  const subscribers = new Set();

  function getState() {
    return state;
  }

  function setState(patch) {
    const next = typeof patch === "function" ? patch(state) : patch;
    state = { ...state, ...next };
    for (const fn of subscribers) {
      try {
        fn(state);
      } catch (err) {
        console.error("Store subscriber failed", err);
      }
    }
  }

  function subscribe(fn) {
    subscribers.add(fn);
    return () => subscribers.delete(fn);
  }

  return { getState, setState, subscribe };
}
