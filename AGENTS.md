# AGENTS.md — working on Binders Pro

Guidance for whoever (human or agent) changes this project next.

## Keep DOCUMENTATION.md in sync

**Any change that adds, removes, or changes a user-visible feature, route,
model field, or config var must update [DOCUMENTATION.md](DOCUMENTATION.md)
in the same change.** That file is the single source of truth for "what
this app does" — treat a feature change without a matching doc update as
incomplete, the same way you'd treat it as incomplete without the code.

- Find (or add) the relevant section and update the **Contents** list.
- New model/field → update the **Data model reference** table.
- New env var → update the **Configuration reference** table (and
  `.env.example`).
- Skip it for pure refactors, bug fixes that just restore already-documented
  behavior, or internal-only helper changes with no user-visible effect.
- If you're unsure whether something is user-visible enough to document,
  document it — a stale README is worse than a slightly over-eager one.

## Keep Ask AI and the MCP server in sync with the app

**Any new record type, field, or mutation added to the UI should get a
matching tool in `ai.py`, in the same change** — a create/update(/archive)
tool mirroring the new pages/*.py route's fields and validation exactly, a
`TOOLS_SCHEMA` entry, a `_DISPATCH` entry, and (if it mutates anything) a
`_MUTATING_TOOLS` entry plus a `_describe_tool_call` branch for the
confirmation banner. `SYSTEM_PROMPT` should mention it too. The point of the
chat assistant and the MCP server is that they're a second way to *use* the
app, not a smaller subset of it — a feature only reachable by clicking
through pages/*.py is a feature Ask AI and MCP can't help with.

- `mcp_server.py` needs no matching change for this: it re-exports
  `ai.TOOLS_SCHEMA`/`ai._execute_tool`/`ai._MUTATING_TOOLS` directly rather
  than keeping its own copy, so anything added to `ai.py` reaches MCP
  clients automatically. If you ever find yourself editing
  `mcp_server.py` to add a tool, stop — it belongs in `ai.py` instead.
- Read tools (`list_*`, `get_*`, `search`, `get_attention`) execute
  immediately; write tools always pause for human confirmation (see
  `_MUTATING_TOOLS` in `ai.py`) — a new mutation is never the exception to
  that.
- A record type with no manual create/edit UI yet (Decision, Note — see
  `insights.subject_url`'s docstring) can still get full write tools in
  `ai.py`: mirror what the model already validates (required fields, status
  enums) rather than what a nonexistent form would submit.
- This is also where a new tool's `search.index_entity(...)` call goes, the
  same as every other write path — see `search.py`.

## Where things go

Pro is Core's page files (`attachments.py`, `auth.py`, `chat.py`, `clients.py`,
`comments.py`, `notifications.py`, `settings.py`, `tasks.py` — same names,
same split, as Core's own `pages/` package) plus Pro's own four: `dashboard.py`,
`crm.py`, `ops.py`, `capture.py`. All thirteen are flat modules under `pages/`,
no per-feature folders. `pages/__init__.py` imports each one directly (`from .
import (...)`, alphabetical) for its route-registering side effects; routes
register through the same `@app.route` decorators everywhere. Adding one is:
create the file, add its name to that import list.

Two rules keep that from rotting:

- **Page files own routes, never models.** Every table lives in the one
  `models.py`. The point of this app is that a Commitment points at a Customer
  which points at a Project — splitting peewee models across files buys tidy
  folders and costs you import cycles and a relational backbone full of
  holes. If a page file needs a new table, add it to `models.py`.
- **Namespace a page file's templates by a subdirectory matching its name** —
  `templates/crm/opportunities_list.html`, rendered as
  `render("crm/opportunities_list.html")`. `TEMPLATE_PATH` is a flat list
  searched in order, so without the prefix two page files can't both have a
  `list.html` and which one wins depends on registration order. The nine
  Core-derived files are the exception: their templates carry no such prefix,
  same as in Core itself.

`pages/_shared.py` holds the handful of query/redirect helpers used by more
than one page file (`_load_comments`, `_active_clients`, `_people`,
`_open_projects`, `_redirect_to_subject`, …) — `dashboard.py`, `crm.py`,
`ops.py` and `capture.py` import straight from it (`from pages._shared import
...`) rather than from whichever route module happened to define one first.
A helper only one file uses stays local to that file instead (see
`settings.py`'s own `_team_members()`).

## Derived state lives in insights.py

**Nothing about time is stored.** There is no `is_overdue` column, no
`at_risk` flag maintained by a job. Overdue, at risk, stalled and unassigned
are all computed at read time from `due_date`/`last_activity_at` against
today, in `insights.py`, which both the dashboard and the control center call.
Two consequences worth internalising before you change anything there:

- A demo database seeded three months ago still reads correctly today. Store
  a flag and it won't.
- The dashboard's "5 overdue commitments" and the control center's overdue
  list are the same function call, so they cannot disagree. Keep it that way —
  if a page needs a new derived number, add it to `insights.py` rather than
  querying for it in a route.

`Opportunity` and `Project` carry `last_activity_at`, which is what "stalled"
reads. Every write path must bump it (`_touch()` in `pages/crm.py` and
`pages/ops.py`); miss one and the record looks abandoned the moment someone
stops editing it from that page.

## The ledger is the layout primitive

The dashboard's attention list, all four ledger sections of `/control`, and the
Capture review are one partial: `dashboard/_ledger.html`, fed rows from
`insights._row()`. Mono gutter carrying elapsed time, hairline between rows,
no card. If you're adding a list of "things that are wrong", render it through
the ledger rather than inventing a fifth list style.

## Capture: text first, always

The box lives at the top of the dashboard, not on a page of its own — text is
the input this app is built around, so it comes before the output. The review
panel replaces it in the same slot, which is why the flow never scrolls.

`POST /capture` writes the `Note` **before** calling the model. If extraction
fails, the words survive — losing someone's meeting notes to an API timeout
would be far worse than showing them an error. The proposal is stored on
`Note.proposal_json` and reviewed at `/?review=<id>`, so state lives on the
row rather than in the session and a refresh mid-review loses nothing.

Everything a model proposes goes through `_clean()` in
`pages/capture_extract.py`, which walks the parsed JSON against
`RECORD_TYPES` and drops unknown types, unknown fields and unusable values.
That allow-list is the boundary between "what a model said" and "what this app
will write". Widen it deliberately, never by adding a passthrough.

## Conventions specific to this codebase

- **Zero pip dependencies beyond gunicorn** (dev-only, for autoreload).
  `bottle`/`peewee` are vendored as plain `.py` files in `vendor/`. Don't add
  a pip dependency without a good reason; if you must, note it in
  `requirements.txt` the way the gunicorn line already does.
- **No app-authored JS beyond what's declaratively necessary.** Mobile nav
  uses the checkbox hack; New Client/Task modals open via
  `commandfor`/`command="show-modal"`, wired by the already-vendored
  `oat.min.js` (native browser support when present, its own click handler
  otherwise) — not custom JS. The Esc-navigation listener and the theme
  toggle are the two deliberate exceptions, because there's no declarative
  way to do either; both are small, single-purpose, and commented inline
  in `layout.html`/`settings.html`. Follow that same shape for anything
  else that genuinely needs script — don't reach for a framework or bundler.
- **Schema changes go through `ensure_schema()`** in `models.py`: add a new
  model to `ALL_MODELS`, or a new column via `_add_column_if_missing()`.
  SQLite can't add a `REFERENCES` column to an existing table, so an added FK
  is a plain `INTEGER` there — peewee resolves it through the model anyway.
  There's no `migrations/` directory and no `peewee-migrate` — this
  idempotent-check-at-startup approach *is* the migration story. Keep it
  that way.
- **Bottle template gotcha:** any source line whose first non-whitespace
  character is `%` is parsed as Python — including inside an HTML comment.
  Don't write something like a `%rebase(...)` call as documentation text on
  its own line in a `.html` template; bottle will try to execute it as a
  statement and throw a `SyntaxError`. (Hit exactly this once — see the git
  history around the Esc-navigation feature.) If you need to reference
  template syntax in a comment, keep it mid-line, not line-initial.
- **Theming:** `style.css`'s `:root` tokens use CSS `light-dark()` keyed off
  the `color-scheme` property, not a `[data-theme="light"]` override block.
  Add new color tokens the same way — `--foo: light-dark(lightVal,
  darkVal);` — rather than introducing a second block to keep in sync. A
  value that can't be a plain `<color>` (e.g. a multi-part `box-shadow`)
  needs decomposing into `light-dark()`-driven intermediate custom
  properties first — see `--shadow-card` for the pattern.
- **Flash messages** (`utils.py: flash()`) always render as `oat.toast()`
  popups via `templates/_toasts.html`, never an inline banner. Route any new
  user-facing confirmation/error through `flash()`, not a hand-rolled
  message element.
- **Generic-over-subject models** (`Comment`/`Attachment`/`Activity`/
  `NoteLink`) key off `subject_type` + `subject_id` rather than a table per
  model. A new commentable/attachable/linkable thing reuses these as-is; don't
  add a parallel `TaskComment` table or a `source_note` FK on six models. Add
  the type to `SUBJECT_TYPES` in `models.py` and to
  `insights.SUBJECT_REGISTRY`, which is the single table mapping a
  `subject_type` to its model, detail route and label — the post-comment
  redirect, the activity feed and the note's linked-records list all resolve
  through it.
- **Every route requires a logged-in user by default** — enforced by a
  `before_request` hook (`_require_login_hook` in `pages/__init__.py`), not a
  per-route decorator. A new route needs *no* annotation to be protected.
  If it must be reachable by a signed-out visitor (a new auth-flow page,
  say), add its `name=` to `PUBLIC_ROUTES` in `utils.py` — forgetting this
  for a route that's supposed to be public shows up immediately as an
  unwanted redirect to `/login`, so it's a load-bearing list, not
  optional bookkeeping. A route with its own non-session auth (like
  `/internal/ai-command`'s `require_internal_secret`) goes in
  `SESSION_INDEPENDENT_PATHS` instead, keyed by path since it's checked
  before routing.
- **Two owner columns on `Task`, on purpose.** `owner` is a `Person` (Pro's
  forms and dashboards use it; it can point at someone with no account).
  `assignee` is Core's `User` and is what the notification path keys off;
  `task_update` keeps it in sync from `owner.user`. Don't collapse them
  without reworking `notify()` and the Core-compatible AI write tools.
- **Task priorities and every Pro status map onto four inks** — grey (not
  started / not urgent), amber (in flight), green (finished / won), red
  (wrong). Sixteen statuses as sixteen colors would be unreadable; as four, a
  person learns the scheme once. Add a new status to the existing
  `.badge-*` / `.status-dot-*` groups in `style.css` rather than picking a
  fresh hue.
- **`--font-serif` is for the briefing voice only** — the dashboard and
  control center headlines, the ledger's primary line, the Capture prompt, a
  note's body, a decision's rationale. Everything else is the sans stack. It's
  a system font stack deliberately: no `@font-face`, no CDN, because this app
  is meant to run offline and self-hosted.

## Verifying a change

- **Templates**: bottle's `SimpleTemplate` can compile a template directly
  (no server needed) to catch syntax errors — `python3 -c "from bottle
  import SimpleTemplate; SimpleTemplate(open('templates/x.html').read())"`
  (run with `vendor/` on `sys.path`).
- **Visual/behavioral changes**: run against a scratch database —
  `SQLITE_PATH=/tmp/scratch.db PORT=8123 python3 app.py` — rather than the
  real `app.db`, so local data doesn't need resetting afterward. Register an
  account and the demo company seeds itself.
- **Anything touching `insights.py`**: check both `/` and `/control` still
  have content. The seed data is tuned so every section on both pages is
  non-empty; a change that empties one is usually a bug in the query, not a
  quiet company.
- **Capture**: test it twice — once with a backend configured, and once with
  `OPENAI_API_KEY` unset and Ollama down. The second path must show the
  "Capture needs an AI backend" message and still keep the note.
