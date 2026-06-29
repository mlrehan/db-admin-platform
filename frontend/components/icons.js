// Inline SVG icon set (stroke-based, currentColor). Returned as strings for easy templating.

const svg = (paths) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;

export const icons = {
  home: svg('<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>'),
  database: svg(
    '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>'
  ),
  editor: svg('<path d="M8 6l-5 6 5 6"/><path d="M16 6l5 6-5 6"/><path d="M13 4l-2 16"/>'),
  schema: svg(
    '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><path d="M10 6.5h4a2 2 0 0 1 2 2V14"/>'
  ),
  table: svg(
    '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 9h18M3 14h18M9 4v16"/>'
  ),
  shield: svg('<path d="M12 3l8 3v6c0 5-3.4 7.7-8 9-4.6-1.3-8-4-8-9V6z"/>'),
  user: svg('<circle cx="12" cy="8" r="4"/><path d="M4 20c0-3.3 3.6-6 8-6s8 2.7 8 6"/>'),
  logout: svg('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>'),
  key: svg('<circle cx="8" cy="15" r="4"/><path d="M10.85 12.15 19 4"/><path d="M16 6l3 3"/><path d="M18 8l2-2"/>'),
  sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  moon: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
  help: svg('<circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4"/><path d="M12 17h.01"/>'),
  menu: svg('<path d="M3 6h18M3 12h18M3 18h18"/>'),
  activity: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
};
