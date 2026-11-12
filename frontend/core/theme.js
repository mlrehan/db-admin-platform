// Theme system: dark (default) / light, persisted in localStorage and applied to <html>.
// A tiny inline script in index.html applies the saved theme before first paint (no flash);
// this module powers runtime toggling.

const KEY = "dbadmin.theme";

export function getTheme() {
  try {
    return localStorage.getItem(KEY) || "dark";
  } catch {
    return "dark";
  }
}

// Fired on window whenever the theme changes, so components that render with their own
// theming (Monaco editor, AG Grid) can follow the app theme live: detail = { theme }.
export const THEME_EVENT = "dbadmin:themechange";

export function isDarkTheme() {
  return (document.documentElement.dataset.theme || getTheme()) !== "light";
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: { theme } }));
}

export function setTheme(theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* ignore */
  }
  applyTheme(theme);
}

export function toggleTheme() {
  const next = getTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}

export function initTheme() {
  applyTheme(getTheme());
}
