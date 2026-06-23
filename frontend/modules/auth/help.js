// Built-in user guide / onboarding. The content adapts to the signed-in user's role:
// administrators see how to create connections and manage access; everyone else sees how to
// use the databases an admin has shared with them.

import { app } from "../../core/context.js";
import { openModal } from "../../components/modal.js";

const RUN_QUERIES = {
  title: "Run queries",
  steps: [
    "Go to the <strong>SQL Editor</strong>. Pick a session and (for server-level connections) the active database in the toolbar.",
    "Type SQL and press <kbd>Run</kbd> or <kbd>Ctrl/⌘ + Enter</kbd>. <strong>Run executes every statement</strong> in the editor, top to bottom.",
    "To run only part of a script, <strong>highlight the statements</strong> you want and press Run — only the selection executes.",
    "<strong>📂 Open file</strong> loads a <code>.sql</code> or <code>.txt</code> file into the editor — e.g. a full schema-and-data dump. Press Run to execute it; the Schema Explorer refreshes automatically so you see the new objects right away.",
    "The <strong>Results</strong> tab shows the last result set; the <strong>Messages</strong> tab lists each statement's outcome. Drag a column border to resize it.",
    "Use the <strong>Schema Explorer</strong> to browse tables, columns and indexes, and the <strong>Data Viewer</strong> for paginated, sortable table data.",
  ],
};

const YOUR_ACCOUNT = {
  title: "Your account",
  steps: [
    "Open the <strong>menu at the top-right</strong> (your email/avatar).",
    "<strong>Active sessions</strong> lists your live connections — disconnect any to free server resources.",
    "<strong>Change password</strong> updates your password (you'll be asked to sign in again).",
    "Switch between <strong>dark and light themes</strong>, and <strong>Sign out</strong> from the same menu (signing out also closes all your open sessions).",
  ],
};

// What an administrator sees.
const ADMIN_SECTIONS = [
  {
    title: "1 · Add a database connection",
    steps: [
      "Open <strong>Connections</strong> and click <em>+ New connection</em> (only administrators can create connections).",
      "Pick the engine (PostgreSQL, MySQL or SQL Server) and enter host, port, user and password.",
      "Leave <strong>Database</strong> blank for a <em>server-level</em> connection — you can then browse and switch between every database on that server.",
      "Click <em>Test</em> to verify, then <em>Connect</em> to open a live <strong>session</strong>.",
      "On a server-level session you can click <strong>＋ Database</strong> in the toolbar to <strong>create a new database</strong> on that server.",
    ],
  },
  { title: "2 · " + RUN_QUERIES.title, steps: RUN_QUERIES.steps },
  {
    title: "3 · Decide who can access what",
    steps: [
      "<strong>You</strong> control all access. Non-admin users start with <em>no</em> access and cannot create connections.",
      "In <strong>Admin → Permissions</strong>, create or edit a <em>grant</em>: choose the user or role, the connection, optionally a specific database and table, and the allowed operations (SELECT, INSERT, …).",
      "The connection is then <em>shared</em> with that user — it appears under their <strong>Connections</strong> and they can do <em>exactly</em> what you granted, nothing more.",
      "To let a trusted user <strong>create databases</strong>, grant them <em>CREATE</em> on the whole connection (leave database and table blank). A ＋ Database button then appears for them.",
      "Manage accounts in <strong>Admin → Users</strong>; review every query in <strong>Admin → Audit log</strong> (append-only). Admins are never restricted by grants.",
    ],
  },
  { title: "4 · " + YOUR_ACCOUNT.title, steps: YOUR_ACCOUNT.steps },
];

// What a non-admin user (DBA / Developer / Viewer) sees.
const USER_SECTIONS = [
  {
    title: "1 · Connect to a database",
    steps: [
      "Open <strong>Connections</strong> — it lists the databases an administrator has <em>shared with you</em>.",
      "Don't see what you need? Connections are created and shared by administrators — <strong>ask your admin for access</strong>.",
      "Click <em>Connect</em> on a connection to open a live <strong>session</strong>. For server-level connections, pick your database in the editor toolbar.",
    ],
  },
  {
    title: "2 · " + RUN_QUERIES.title,
    steps: [
      ...RUN_QUERIES.steps,
      "You can run only the operations you've been granted (e.g. SELECT). Anything outside your access is safely blocked with a clear message.",
      "You only see the databases, schemas and tables shared with you — <strong>system objects are hidden</strong>. Some actions (like creating a database) appear only if your administrator granted them.",
    ],
  },
  { title: "3 · " + YOUR_ACCOUNT.title, steps: YOUR_ACCOUNT.steps },
];

function sectionsForRole(role) {
  return role === "admin" ? ADMIN_SECTIONS : USER_SECTIONS;
}

export function helpContent(role) {
  const wrap = document.createElement("div");
  wrap.className = "help-content";
  wrap.innerHTML = sectionsForRole(role)
    .map(
      (s) => `
      <section class="help-section">
        <h4>${s.title}</h4>
        <ol>${s.steps.map((t) => `<li>${t}</li>`).join("")}</ol>
      </section>`
    )
    .join("");
  return wrap;
}

export function openHelp() {
  const role = app.auth?.user?.role;
  openModal({ title: "Getting started", content: helpContent(role), width: 560 });
}
