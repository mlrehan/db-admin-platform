// Monaco binding for SQL IntelliSense: maps the pure engines (core/sql-intellisense.js and
// core/sql-functions.js) onto Monaco's providers:
//
//   * completion      — tables, columns, aliases, keywords, routines and the full built-in
//                       function catalog; inside a call, the allowed values of the parameter
//                       at the cursor (e.g. dateparts in DATEADD( … ))
//   * signature help  — parameter hints: DATEADD(datepart, number, date) with the active
//                       parameter highlighted and documented; overloads; user procedures/functions
//   * hover           — tables, aliases, columns and functions
//
// Monaco supplies the widgets with full keyboard (↑/↓, Enter, Tab, Esc, Ctrl+Shift+Space) and
// mouse support; their colours follow the editor theme (light/dark). This file only decides WHAT
// to show. The active session/database/dialect is read at request time from the session store,
// so the same providers serve every editor instance.

import { sessionStore } from "../core/session-state.js";
import { loadCatalog, peekIndex } from "../core/catalog-store.js";
import {
  analyzeContext,
  buildIndex,
  findObject,
  resolveMember,
  suggest,
  tokenize,
  tableRefs,
  statementBounds,
} from "../core/sql-intellisense.js";
import {
  builtinFunctions,
  callContext,
  paramDoc,
  parameterValueItems,
  routineSignature,
  signatureHelp,
} from "../core/sql-functions.js";

const EMPTY = buildIndex({ objects: [], routines: [] });
const FIRST_LOAD_WAIT_MS = 1500;
let registered = false;

function context() {
  const { sessionId, engine, database } = sessionStore.getState();
  return { sessionId, database, dialect: engine || "postgresql" };
}

async function currentIndex(sessionId, database) {
  const ready = peekIndex(sessionId, database);
  const loading = sessionId ? loadCatalog(sessionId, database) : null; // refresh if stale
  if (ready || !loading) return { index: ready || EMPTY, pending: !ready && !!loading };
  // First use: wait briefly so the very first suggestion list already has the tables.
  const index = await Promise.race([
    loading.catch(() => null),
    new Promise((r) => setTimeout(() => r(null), FIRST_LOAD_WAIT_MS)),
  ]);
  return { index: index || EMPTY, pending: !index };
}

// --- documentation (markdown) ----------------------------------------------------------------

const code = (s) => "```sql\n" + s + "\n```";

function paramMarkdown(p) {
  const tags = [p.optional ? "optional" : "", p.variadic ? "repeatable" : ""].filter(Boolean).join(", ");
  const doc = paramDoc(p);
  return `**${p.name}**${tags ? ` _(${tags})_` : ""}${doc ? ` — ${doc}` : ""}`;
}

function signatureMarkdown(sig) {
  const parts = [];
  if (sig.doc) parts.push(sig.doc);
  if (sig.returns) parts.push(`**Returns:** \`${sig.returns}\``);
  return parts.join("\n\n");
}

function functionDocs(sigs) {
  const blocks = sigs.map((s) => code(s.label + (s.window ? " OVER (…)" : "")));
  const main = sigs[0];
  const params = main.params.length ? "\n\n" + main.params.map((p) => `- ${paramMarkdown(p)}`).join("\n") : "";
  return `${blocks.join("\n")}\n\n${signatureMarkdown(main)}${params}`;
}

function findRoutine(index, schema, name) {
  const n = String(name || "").toLowerCase();
  const s = schema ? String(schema).toLowerCase() : null;
  return (index.routines || []).find(
    (r) => r.name.toLowerCase() === n && (!s || (r.schema_name || "").toLowerCase() === s)
  ) || null;
}

/** Signatures for the call at the cursor: built-in function, or a user procedure/function. */
function signaturesFor(call, dialect, index) {
  const builtin = builtinFunctions(dialect).get(String(call.name).toUpperCase());
  // A schema-qualified name or an EXEC target is a user routine, even if it shadows a built-in.
  if (builtin && !call.schema && !call.procedureCall) return builtin;
  const routine = findRoutine(index, call.schema, call.name);
  if (routine) return [routineSignature(routine, !!call.procedureCall)];
  return builtin || null;
}

export function registerSqlCompletion(monaco) {
  if (registered || !monaco?.languages) return;
  registered = true;
  const K = monaco.languages.CompletionItemKind;
  const KIND = {
    table: K.Struct, view: K.Interface, column: K.Field, schema: K.Module, routine: K.Method,
    function: K.Function, keyword: K.Keyword, alias: K.Variable, value: K.EnumMember,
  };
  const SNIPPET = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;
  const TRIGGER_CHAR = monaco.languages.CompletionTriggerKind.TriggerCharacter;
  // After inserting a function, open the parameter hints straight away (like SSMS).
  const SHOW_PARAMETER_HINTS = { id: "editor.action.triggerParameterHints", title: "Parameter hints" };

  monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "(", ","],
    async provideCompletionItems(model, position, ctx, token) {
      const { sessionId, database, dialect } = context();
      const text = model.getValue();
      const offset = model.getOffsetAt(position);
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };

      // Inside a function call whose current parameter takes a fixed set of values
      // (DATEADD( → dateparts, CAST(x AS → data types, DATE_TRUNC( → 'month' …): offer those.
      const call = callContext(text, offset, dialect);
      const values = parameterValueItems(call, dialect);
      if (values) {
        let valueRange = range;
        const lineText = model.getLineContent(position.lineNumber);
        const quoteAt = lineText.lastIndexOf("'", position.column - 2);
        if (call.inString && quoteAt !== -1) {
          // Inside '…': replace everything typed after the opening quote ('start of mo|).
          valueRange = { ...range, startColumn: quoteAt + 2 };
        }
        return {
          suggestions: values.items.map((it) => ({
            label: { label: it.label, description: it.detail },
            kind: K.EnumMember,
            detail: `${values.sig.name} · ${values.param.name}`,
            documentation: { value: paramMarkdown(values.param) },
            insertText: it.insertText,
            filterText: it.label,
            sortText: it.sortText,
            range: valueRange,
          })),
        };
      }

      const analysis = analyzeContext(text, offset, dialect);
      if (analysis.kind === "none") return { suggestions: [] };
      // A space/comma only opens the list where an object or column is expected
      // (after FROM, JOIN, EXEC, WHERE …) — never in the middle of ordinary typing.
      if (
        ctx.triggerKind === TRIGGER_CHAR &&
        (ctx.triggerCharacter === " " || ctx.triggerCharacter === ",") &&
        !["object", "routine", "column"].includes(analysis.kind)
      ) {
        return { suggestions: [] };
      }
      const { index, pending } = await currentIndex(sessionId, database);
      if (token?.isCancellationRequested) return { suggestions: [] };
      const { items, incomplete } = suggest(analysis, index, dialect);
      return {
        // "incomplete" makes Monaco ask again as the user keeps typing (large catalogs are
        // capped server-side of the list, and the first load may still be in flight).
        incomplete: incomplete || pending,
        suggestions: items.map((it) => {
          const item = {
            label: it.label,
            kind: KIND[it.kind] ?? K.Text,
            detail: it.detail,
            insertText: it.insertText,
            insertTextRules: it.snippet ? SNIPPET : undefined,
            filterText: it.filterText || it.label,
            sortText: it.sortText,
            range,
          };
          if (it.kind === "function" && it.signatures) {
            const sig = it.signatures[0];
            // Show the parameter list right in the list row, return type on the right.
            item.label = {
              label: it.label,
              detail: sig.label.slice(sig.name.length),
              description: sig.returns || undefined,
            };
            item.detail = it.signatures.length > 1 ? `${sig.label}  (+${it.signatures.length - 1} overload)` : sig.label;
            item.documentation = { value: functionDocs(it.signatures) };
            item.command = SHOW_PARAMETER_HINTS;
          } else if (["column", "table", "view", "alias"].includes(it.kind) && it.detail) {
            // Data type / owner on the right of the row (e.g. "date NOT NULL — Admission").
            item.label = { label: it.label, description: it.detail };
          }
          if (it.kind === "routine" && it.routine) {
            const sig = routineSignature(it.routine, analysis.kind === "routine" && dialect === "mssql");
            item.documentation = { value: `${code(sig.label)}\n\n${signatureMarkdown(sig)}` };
            const params = Array.isArray(it.routine.params) ? `(${it.routine.params.join(", ")})` : "";
            item.label = {
              label: it.label,
              detail: params || undefined,
              description: it.routine.returns || (it.routine.kind === "procedure" ? "procedure" : "function"),
            };
          }
          return item;
        }),
      };
    },
  });

  // Parameter hints (SSMS "Parameter Info"): which function, which parameter, what it takes.
  monaco.languages.registerSignatureHelpProvider("sql", {
    // Space too: keyword-separated parameters (CAST(x AS …), EXTRACT(y FROM …)) and re-opening
    // the hint after another popup (e.g. a column list) closed it. Outside a call → null.
    signatureHelpTriggerCharacters: ["(", ",", " "],
    signatureHelpRetriggerCharacters: [",", " ", ")"],
    provideSignatureHelp(model, position) {
      const { sessionId, database, dialect } = context();
      const call = callContext(model.getValue(), model.getOffsetAt(position), dialect);
      if (!call) return null;
      const sigs = signaturesFor(call, dialect, peekIndex(sessionId, database) || EMPTY);
      const help = signatureHelp(call, sigs);
      if (!help) return null;
      return {
        value: {
          signatures: help.signatures.map((sig) => ({
            label: sig.label,
            documentation: { value: signatureMarkdown(sig) },
            parameters: sig.params.map((p) => ({
              label: [p.start, p.end],
              // paramDoc already lists the allowed values ("One of: year, quarter, …"); the
              // full list with meanings is in the completion list, so the hint stays compact.
              documentation: { value: paramMarkdown(p) },
            })),
          })),
          activeSignature: help.activeSignature,
          activeParameter: help.activeParameter,
        },
        dispose() {},
      };
    },
  });

  // Quick Info (SSMS-style): hover a table, alias, column or function to see its definition.
  monaco.languages.registerHoverProvider("sql", {
    async provideHover(model, position) {
      const word = model.getWordAtPosition(position);
      if (!word) return null;
      const { sessionId, database, dialect } = context();
      const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
      const lineText = model.getLineContent(position.lineNumber);
      // Function call: the word is followed by "(".
      if (/^\s*\(/.test(lineText.slice(word.endColumn - 1))) {
        const before = lineText.slice(0, word.startColumn - 1);
        const qualified = /\.\s*$/.test(before);
        const builtin = !qualified && builtinFunctions(dialect).get(word.word.toUpperCase());
        if (builtin) return { range, contents: [{ value: functionDocs(builtin) }] };
        const index = peekIndex(sessionId, database);
        const routine = index && findRoutine(index, null, word.word);
        if (routine) {
          const sig = routineSignature(routine);
          return { range, contents: [{ value: `${code(sig.label)}\n\n${signatureMarkdown(sig)}` }] };
        }
      }
      const index = peekIndex(sessionId, database);
      if (!index) return null;
      const text = model.getValue();
      const offset = model.getOffsetAt({ lineNumber: position.lineNumber, column: word.endColumn });
      const { start, end } = statementBounds(text, offset, dialect);
      const stmt = text.slice(start, end);
      const tables = tableRefs(tokenize(stmt, dialect));
      const before = lineText.slice(0, word.startColumn - 1);
      const qual = before.match(/([A-Za-z_][\w$#@]*|\[[^\]]+\]|"[^"]+"|`[^`]+`)\.\s*$/);
      if (qual) {
        const owner = qual[1].replace(/^[[\]"`]|[[\]"`]$/g, "");
        const target = resolveMember([owner], tables, index);
        const col = target.type === "columns"
          ? (target.object.columns || []).find((c) => c.name.toLowerCase() === word.word.toLowerCase())
          : null;
        if (col) {
          return { range, contents: [{ value: `**${target.object.name}.${col.name}**  \n\`${col.type}\`${col.nullable ? "" : " · NOT NULL"}` }] };
        }
      }
      const alias = tables.find((t) => (t.alias || "").toLowerCase() === word.word.toLowerCase());
      const obj = alias?.name ? findObject(index, alias.schema, alias.name) : findObject(index, null, word.word);
      if (!obj) return null;
      const cols = (obj.columns || []).slice(0, 25).map((c) => `- \`${c.name}\` ${c.type}`).join("\n");
      const more = (obj.columns || []).length > 25 ? `\n- … ${(obj.columns || []).length - 25} more` : "";
      return {
        range,
        contents: [
          { value: `**${obj.kind === "view" ? "View" : "Table"}** \`${obj.schema_name ? `${obj.schema_name}.` : ""}${obj.name}\`${alias ? ` (alias \`${alias.alias}\`)` : ""}` },
          { value: cols + more },
        ],
      };
    },
  });
}
