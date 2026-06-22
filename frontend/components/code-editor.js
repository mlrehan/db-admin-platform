// SQL code editor. Uses Monaco when it loads from CDN; otherwise falls back to a styled
// monospace textarea (fully functional). Exposes getValue()/setValue()/focus(). Phase 9 will
// vendor Monaco locally to remove the CDN dependency.

import { config } from "../core/config.js";

const MONACO_BASE = config.monacoBase;

function loadMonaco() {
  if (window.__monacoPromise) return window.__monacoPromise;
  window.__monacoPromise = new Promise((resolve, reject) => {
    if (window.monaco) return resolve(window.monaco);
    const timer = setTimeout(() => reject(new Error("monaco load timeout")), 8000);
    const script = document.createElement("script");
    script.src = `${MONACO_BASE}/loader.js`;
    script.onload = () => {
      window.require.config({ paths: { vs: MONACO_BASE } });
      window.require(["vs/editor/editor.main"], () => {
        clearTimeout(timer);
        resolve(window.monaco);
      });
    };
    script.onerror = () => {
      clearTimeout(timer);
      reject(new Error("monaco load failed"));
    };
    document.head.appendChild(script);
  });
  return window.__monacoPromise;
}

export class CodeEditor extends HTMLElement {
  connectedCallback() {
    this._value = this.getAttribute("value") || "";
    this.classList.add("code-editor");
    this._textarea = document.createElement("textarea");
    this._textarea.className = "code-fallback input";
    this._textarea.spellcheck = false;
    this._textarea.value = this._value;
    this._textarea.placeholder = "-- Write SQL here";
    this.appendChild(this._textarea);
    this._textarea.addEventListener("keydown", (e) => {
      // Ctrl/Cmd+Enter runs the query (consumed by the parent view).
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        this.dispatchEvent(new CustomEvent("run", { bubbles: true }));
      }
    });

    this._tryMonaco();
  }

  async _tryMonaco() {
    try {
      const monaco = await loadMonaco();
      const host = document.createElement("div");
      host.className = "monaco-host";
      this.appendChild(host);
      this._textarea.classList.add("hidden");
      this._monaco = monaco.editor.create(host, {
        value: this._value,
        language: "sql",
        theme: "vs-dark",
        minimap: { enabled: false },
        fontSize: 13,
        fontFamily: "var(--font-mono)",
        automaticLayout: true,
        scrollBeyondLastLine: false,
        wordWrap: "on",
        padding: { top: 10 },
      });
      this._monaco.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
        () => this.dispatchEvent(new CustomEvent("run", { bubbles: true }))
      );
    } catch {
      // Fallback textarea stays; nothing to do.
    }
  }

  getValue() {
    return this._monaco ? this._monaco.getValue() : this._textarea.value;
  }

  // Returns the highlighted text, or "" if there is no selection.
  getSelectedText() {
    if (this._monaco) {
      const sel = this._monaco.getSelection();
      if (sel && !sel.isEmpty()) {
        return this._monaco.getModel().getValueInRange(sel);
      }
      return "";
    }
    const ta = this._textarea;
    if (ta && ta.selectionStart !== ta.selectionEnd) {
      return ta.value.substring(ta.selectionStart, ta.selectionEnd);
    }
    return "";
  }

  setValue(text) {
    this._value = text || "";
    if (this._monaco) this._monaco.setValue(this._value);
    if (this._textarea) this._textarea.value = this._value;
  }

  focus() {
    (this._monaco || this._textarea)?.focus();
  }
}

customElements.define("code-editor", CodeEditor);
