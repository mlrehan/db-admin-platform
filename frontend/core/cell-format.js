// Result-cell formatting shared by every results renderer (AG Grid and the built-in table).
// Pure — no DOM — and unit-tested.
//
// Values arrive exactly as the database returned them (see backend app/utils/serialization.py):
// SQL NULL is JSON null, '' stays '', 0 and false stay 0 and false. Decimals, integers beyond
// ±2^53 and dates travel as strings, so each column also carries a `kind` (integer, decimal,
// float, boolean, date, datetime, time, interval, binary, uuid, json, text) inferred from the
// driver values on the server; formatting, alignment and sorting use it.

export const NUMERIC_KINDS = new Set(["integer", "decimal", "float"]);

export function isNumericKind(kind) {
  return NUMERIC_KINDS.has(kind);
}

// Short labels shown next to a column name (and in its tooltip).
export const KIND_LABELS = {
  integer: "int", decimal: "dec", float: "float", boolean: "bool", date: "date",
  datetime: "datetime", time: "time", interval: "interval", binary: "binary", uuid: "uuid",
  json: "json", text: "text", other: "value",
};

export function kindLabel(kind) {
  return KIND_LABELS[kind] || "null";
}

const SPECIAL_NUMBERS = new Set(["NaN", "Infinity", "-Infinity"]);
const MAX_DISPLAY_CHARS = 2000; // longer values are shortened in the cell; the tooltip has more

function base64ToHex(b64, maxBytes) {
  try {
    const bin = atob(b64);
    let hex = "";
    for (let i = 0; i < Math.min(bin.length, maxBytes); i++) {
      hex += bin.charCodeAt(i).toString(16).padStart(2, "0").toUpperCase();
    }
    return { hex, bytes: bin.length };
  } catch {
    return null;
  }
}

/**
 * How to display one value.
 * @returns {{text: string, cls: string, title: string|null}}
 *   text  — what the cell shows (plain text; renderers escape it)
 *   cls   — CSS class for the value's role (cell-null, cell-empty, cell-num, …)
 *   title — tooltip text, or null
 */
export function formatCell(value, kind = null) {
  if (value === null || value === undefined) {
    return { text: "NULL", cls: "cell-null", title: "SQL NULL (no value)" };
  }
  if (value === "") {
    return { text: "(empty)", cls: "cell-empty", title: "Empty string ('')" };
  }
  if (typeof value === "boolean") {
    return { text: value ? "true" : "false", cls: `cell-bool ${value ? "is-true" : "is-false"}`, title: null };
  }
  if (typeof value === "number" || isNumericKind(kind)) {
    const text = String(value);
    return { text, cls: SPECIAL_NUMBERS.has(text) ? "cell-num cell-special" : "cell-num", title: null };
  }
  if (typeof value === "object") {
    let text;
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
    return shorten(text, "cell-json");
  }
  const text = String(value);
  if (kind === "datetime") {
    // ISO 8601 "2020-01-01T09:30:00" reads better as "2020-01-01 09:30:00" (value unchanged).
    return { text: text.replace(/^(\d{4}-\d{2}-\d{2})T/, "$1 "), cls: "cell-date", title: text };
  }
  if (kind === "date" || kind === "time" || kind === "interval") {
    return { text, cls: "cell-date", title: null };
  }
  if (kind === "binary") {
    const decoded = base64ToHex(text, 32);
    if (decoded) {
      const more = decoded.bytes > 32 ? "…" : "";
      return { text: `0x${decoded.hex}${more}`, cls: "cell-binary", title: `${decoded.bytes} bytes` };
    }
  }
  if (kind === "uuid") return { text, cls: "cell-mono", title: null };
  if (/^\s+$/.test(text)) {
    // Whitespace-only is not empty — make it visible instead of a blank-looking cell.
    const n = text.length;
    return { text: `(${n} whitespace char${n === 1 ? "" : "s"})`, cls: "cell-empty", title: JSON.stringify(text) };
  }
  if (/[\r\n]/.test(text)) {
    // Keep each row one line: show line breaks as ↵, full text in the tooltip.
    const flat = text.replace(/\r\n|\r|\n/g, " ↵ ");
    return { text: clip(flat), cls: "cell-text", title: clip(text, 4000) };
  }
  return shorten(text, "cell-text");
}

function clip(text, max = MAX_DISPLAY_CHARS) {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function shorten(text, cls) {
  // Long values: the cell ellipsizes (CSS); the tooltip shows the full value.
  // Anything long enough to be ellipsized in a narrow column gets a tooltip.
  return { text: clip(text), cls, title: text.length > 18 ? clip(text, 4000) : null };
}

/** Escape text for innerHTML. */
export function escapeCell(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Cell HTML (used by both renderers so NULL / empty / numbers look identical everywhere). */
export function cellHtml(value, kind) {
  const f = formatCell(value, kind);
  return `<span class="${f.cls}">${escapeCell(f.text)}</span>`;
}

const INT_RE = /^-?\d+$/;

function compareNumbers(a, b) {
  // Exact for big integers sent as strings; numeric for decimals / floats.
  if (typeof a === "string" && typeof b === "string" && INT_RE.test(a) && INT_RE.test(b)) {
    const x = BigInt(a);
    const y = BigInt(b);
    return x < y ? -1 : x > y ? 1 : 0;
  }
  const x = Number(a);
  const y = Number(b);
  if (Number.isNaN(x) || Number.isNaN(y)) {
    if (Number.isNaN(x) && Number.isNaN(y)) return 0;
    return Number.isNaN(x) ? 1 : -1; // NaN after every number
  }
  return x - y;
}

const collator = typeof Intl !== "undefined" ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }) : null;

/**
 * Sort comparator for a column (ascending). NULLs sort first (like SQL Server's ascending
 * ORDER BY); empty strings come right after NULL; numbers compare numerically even when they
 * arrive as strings (decimals, big integers).
 */
export function compareCells(a, b, kind = null) {
  const an = a === null || a === undefined;
  const bn = b === null || b === undefined;
  if (an || bn) return an && bn ? 0 : an ? -1 : 1;
  if (a === b) return 0;
  if (typeof a === "boolean" || typeof b === "boolean") return Number(a) - Number(b);
  if (isNumericKind(kind) || (typeof a === "number" && typeof b === "number")) return compareNumbers(a, b);
  const x = typeof a === "object" ? JSON.stringify(a) : String(a);
  const y = typeof b === "object" ? JSON.stringify(b) : String(b);
  return collator ? collator.compare(x, y) : x < y ? -1 : x > y ? 1 : 0;
}

/**
 * Initial column width in px from the header and a sample of values: wide enough for typical
 * content, capped so one long text column can't push everything off screen (users can still
 * drag it wider; the grid scrolls horizontally).
 */
/** Smallest width that still shows the whole column name (used as the column's minimum). */
export function headerMinWidth(name, kind, { max = 220 } = {}) {
  return Math.round(Math.min(max, Math.max(String(name).length * 7.4, kindLabel(kind).length * 6.2) + 40));
}

const MONO_KINDS = new Set(["integer", "decimal", "float", "date", "datetime", "time", "interval", "binary", "uuid", "json"]);

export function estimateColumnWidth(name, values, kind, { min = 72, max = 380, sample = 200 } = {}) {
  // Header: bold name (the kind sits on a second line underneath) + padding + sort/menu icon.
  const header = Math.max(String(name).length * 7.4, kindLabel(kind).length * 6.2) + 44;
  // Values: monospace kinds are wider per character than proportional text.
  const charPx = MONO_KINDS.has(kind) ? 7.6 : 7.0;
  let longest = 0;
  const n = Math.min(values.length, sample);
  for (let i = 0; i < n; i++) {
    const len = formatCell(values[i], kind).text.length;
    if (len > longest) longest = len;
    if (longest * charPx > max) break;
  }
  const content = longest * charPx + 28;
  return Math.round(Math.min(max, Math.max(min, header, content)));
}

/** Text for CSV export: NULL → empty field; objects as JSON; everything else unchanged. */
export function csvValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

/**
 * One CSV field. SQL NULL is an EMPTY, UNQUOTED field while an empty string is a quoted ""
 * (the PostgreSQL COPY … CSV convention), so NULL and '' stay distinguishable after export.
 */
export function csvField(value) {
  if (value === null || value === undefined) return "";
  const s = String(csvValue(value));
  return s === "" || /[",\r\n]/.test(s) || /^\s|\s$/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV document (RFC 4180, CRLF) with a UTF-8 BOM so Excel opens non-ASCII text correctly. */
export function toCsv(columnNames, rows) {
  const lines = [columnNames.map((n) => csvField(String(n))).join(",")];
  for (const r of rows) lines.push(r.map(csvField).join(","));
  return `﻿${lines.join("\r\n")}\r\n`;
}
