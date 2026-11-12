// Monaco binding for SQL IntelliSense: maps the pure engine (core/sql-intellisense.js) onto
// Monaco's completion + hover providers. Monaco supplies the suggestion widget with full
// keyboard (↑/↓, Enter, Tab, Esc) and mouse support; this file only decides WHAT to suggest.
//
// The active session/database/dialect is read at request time from the shared session store,
// so the same provider serves every editor instance.

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

export function registerSqlCompletion(monaco) {
  if (registered || !monaco?.languages) return;
  registered = true;
  const K = monaco.languages.CompletionItemKind;
  const KIND = {
    table: K.Struct, view: K.Interface, column: K.Field, schema: K.Module, routine: K.Method,
    function: K.Function, keyword: K.Keyword, alias: K.Variable,
  };
  const SNIPPET = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;
  const TRIGGER_CHAR = monaco.languages.CompletionTriggerKind.TriggerCharacter;

  monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " ", "(", ","],
    async provideCompletionItems(model, position, ctx, token) {
      const { sessionId, database, dialect } = context();
      const text = model.getValue();
      const analysis = analyzeContext(text, model.getOffsetAt(position), dialect);
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
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };
      return {
        // "incomplete" makes Monaco ask again as the user keeps typing (large catalogs are
        // capped server-side of the list, and the first load may still be in flight).
        incomplete: incomplete || pending,
        suggestions: items.map((it) => ({
          label: it.label,
          kind: KIND[it.kind] ?? K.Text,
          detail: it.detail,
          insertText: it.insertText,
          insertTextRules: it.snippet ? SNIPPET : undefined,
          filterText: it.filterText || it.label,
          sortText: it.sortText,
          range,
        })),
      };
    },
  });

  // Quick Info (SSMS-style): hover a table, alias or column to see its definition.
  monaco.languages.registerHoverProvider("sql", {
    async provideHover(model, position) {
      const word = model.getWordAtPosition(position);
      if (!word) return null;
      const { sessionId, database, dialect } = context();
      const index = peekIndex(sessionId, database);
      if (!index) return null;
      const text = model.getValue();
      const offset = model.getOffsetAt({ lineNumber: position.lineNumber, column: word.endColumn });
      const { start, end } = statementBounds(text, offset, dialect);
      const stmt = text.slice(start, end);
      const tables = tableRefs(tokenize(stmt, dialect));
      const lineText = model.getLineContent(position.lineNumber);
      const before = lineText.slice(0, word.startColumn - 1);
      const qual = before.match(/([A-Za-z_][\w$#@]*|\[[^\]]+\]|"[^"]+"|`[^`]+`)\.\s*$/);
      const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
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
