# Scalar Pro — Feature Documentation

This describes what's actually built, for a person (or an agent) picking up
this project without prior context. Code comments cover *why* a given line
is written the way it is; this file covers *what the product does*, page by
page. Keep it in sync with the code — see [AGENTS.md](AGENTS.md).

## Contents

- [Overview](#overview)
- [Accounts, team & access](#accounts-team--access)
- [Dashboard](#dashboard)
- [Control](#control)
- [Capture — text into records](#capture--text-into-records)
- [Customers](#customers)
- [Opportunities](#opportunities)
- [Projects](#projects)
- [Tasks](#tasks)
- [Commitments](#commitments)
- [Decisions](#decisions)
- [Notes](#notes)
- [People](#people)
- [Demo data](#demo-data)
- [Comments, attachments & activity](#comments-attachments--activity)
- [Notifications](#notifications)
- [Ask AI (chat assistant)](#ask-ai-chat-assistant)
- [MCP server](#mcp-server)
- [Scheduled jobs & reminders](#scheduled-jobs--reminders)
- [Settings & appearance](#settings--appearance)
- [Error pages](#error-pages)
- [Navigation & keyboard](#navigation--keyboard)
- [Email](#email)
- [Interface & behavior](#interface--behavior)
- [Data model reference](#data-model-reference)
- [Configuration reference](#configuration-reference)

## Overview

A small single-tenant startup operating system. Underneath: **Customers**,
**People**, **Projects**, **Tasks**, **Opportunities**, **Commitments**,
**Decisions** and **Notes**, sharing comments, attachments, an activity log
and notifications. On top: two screens that read the whole company back to
you, and a Capture box that turns pasted prose into records.

"Single-tenant" means one running instance serves one team — no workspace/org
switcher; whoever signs up first is the owner, everyone else joins by invite.

Three ideas are worth knowing before reading the rest:

- **Nothing about time is stored.** Overdue, at risk and stalled are computed
  at read time from due dates and last-activity timestamps. A database seeded
  months ago still reads correctly today.
- **The dashboards can't disagree.** Both pages call the same functions in
  `insights.py`, so "5 overdue commitments" on one is the same list as the
  other.
- **Structure never replaces text.** Every record Capture creates links back
  to the note it came from, and the note is kept verbatim.

Server-rendered [Bottle](https://bottlepy.org) + [peewee](http://docs.peewee-orm.com)
over SQLite, styled with the [oat.css](https://oat.style) design-system
library. No build step, no Node, no pip install for the app itself — bottle
and peewee are vendored as plain `.py` files in `vendor/`. See the
[README](README.md) for how to run it.

## Accounts, team & access

- **First run**: `/register` creates the first user, who becomes **owner**,
  and seeds an entire fictional company (see [Demo data](#demo-data)) so the
  dashboard has something to say from the first page load. Once any team member exists, `/register` redirects to
  `/login` — there's no open sign-up after that.
- **Roles**: `owner`, `admin`, `member` (`TeamMember.role`). The only
  role-gated action is **sending an invite** (owner/admin — `@require_role`
  in `pages/auth.py`). Everything else (Clients/Tasks CRUD, comments,
  attachments) is open to any signed-in team member; roles are otherwise
  informational (shown in the Settings team table).
- **Inviting**: an owner/admin enters an email on the Settings page, which
  emails a tokenized `/invite/<token>` accept link (see [Email](#email) for
  what happens if email isn't configured). The invite's role defaults to
  `member`. Accepting sets a name + password and logs the new user in.
- **Password reset**: `/forgot-password` emails a time-limited
  `/reset-password/<token>` link. Always shows the same "if that email has
  an account…" message either way, so the form can't be used to enumerate
  registered emails.
- **Sessions**: a single signed, HMAC'd cookie (see
  [Interface & behavior](#interface--behavior)) — no server-side session
  store.

## Dashboard

`/` — the CEO's thirty seconds, in the order a person would want it: the
sentences first, the numbers second. Deliberately not a BI dashboard.

- **Headline** — "Good morning, {name}." and a standfirst counting what needs
  attention ("Three things need your attention.").
- **Capture** — the box you write into, first on the page. See
  [Capture](#capture--text-into-records).
- **Attention** — up to six ledger rows, worst first: overdue commitments
  collapsed into one line, opportunity next-actions past due, projects behind
  schedule, and deals with no activity for 10+ days. Each row links to the
  thing itself. Deduplicated by target, so a deal that is both late and quiet
  takes one line, not two.
- **Snapshot** — five numbers on one strip: pipeline value, open deals, active
  projects, overdue commitments, tasks due in the next seven days. "Due this
  week" excludes anything already late, since that's the tile beside it.
- **What's changed** — the last seven days of the `Activity` log, grouped by
  day and rendered as sentences ("Acme moved from Lead to Active"). Core
  writes these rows on every mutation; Pro is where they're finally read.
- **Decisions** — any due for review in the next 14 days, then the five most
  recent.
(Capture sits above all of this — see below.)

## Control

`/control` — the same data asked a harsher question: *What isn't under
control?* Five sections, none truncated:

- **Overdue** — one merged list across tasks, commitments, opportunity
  next-actions and projects, sorted by days late. A COO doesn't care which
  table a slipped thing lives in, only that it's eleven days late.
- **At risk** — projects that are flagged `at_risk`/`blocked`, past due, due
  within seven days with work outstanding, or carrying overdue tasks. Rendered
  as "Website redesign — due Friday · 7 tasks remaining · 2 overdue".
- **Stalled** — opportunities with no activity for 7+ days (open stages only)
  and projects quiet for 14+. Nothing is late about these; that's why they
  appear nowhere else.
- **Unassigned** — counts of open tasks, deals, projects and commitments with
  no owner, each linking to the list where it can be fixed.
- **This week's commitments** — a Person / Commitment / Due / Status table
  answering "who promised what, and are they doing it?".

## Capture — text into records

The box at the top of the dashboard, directly under the headline — the first
thing on the page, because text is the input this whole app is built around.
Not a separate page: the whole flow happens on `/`, as three plain POSTs with
no JavaScript, and the review panel opens in the same slot so nothing scrolls.

1. **Paste** — a meeting note, an email, a brain dump. Press **Read this**.
2. **Read** — the note is saved **first**, then sent to the model, which
   returns a JSON proposal. If extraction fails the note survives, and the
   message says what to fix rather than apologising.
3. **Review** — the same panel comes back showing the proposal as a ledger,
   one row per proposed record with a four-letter type tag (`CUST` `WHO`
   `DEAL` `PROJ` `TASK` `PROM` `DECN`) and a ticked checkbox. Header reads
   "Scalar found 6 records."; the button reads **Create 6 records**. The
   proposal lives on the note row and the state is a URL (`/?review=<id>`),
   so a refresh loses nothing.
4. **Create** — only ticked records are created. Customers and people are
   matched by name, case-insensitively, so a note about Acme lands *on* Acme
   rather than beside a second copy of it. Each record gets a `NoteLink` back
   to the note and an `extracted` activity row, then you're redirected to the
   customer — where the deal, the promises and the note are now sitting
   together.

**Discard** throws away the proposal and keeps the note. Text is never what
gets discarded.

Extracted record types and their fields are declared once, in
`RECORD_TYPES` (`pages/capture_extract.py`), which drives the prompt, the
validation and the review labels together. Anything the model returns outside
that allow-list — an unknown record type, an unknown field, an unparseable
date — is dropped before it can reach the database.

Two mappings worth knowing: a requirement a customer states ("they need SSO
before signing") becomes a **task** against that customer, and a competitor
mentioned in passing goes into the customer's **notes** — neither gets a
record type of its own.

**Without an AI backend** (`OPENAI_API_KEY` unset and no Ollama running),
Capture saves the note and says: *"Capture needs an AI backend. Set
OPENAI_API_KEY, or start Ollama and try again."* Everything else in the app
is unaffected.

## Customers

`/clients` — a searchable table (name/email/company, via `?q=`) of
non-archived customers. **New customer** opens a modal dialog with name
(required), email, phone, company, status (lead/active/inactive), and
freeform notes. Clicking a row opens `/clients/<id>`, which is the record
everything else hangs off:

- Edit form for every field above, plus **Archive** (soft delete —
  `archived_at` is set, not a real row delete, so activity/comments/
  attachments referencing it survive).
- **Opportunities**, **Projects**, **Commitments**, **Tasks**, **Decisions**
  and **Notes** for this customer, each linking through.
- A file-upload **Attachments** section and the shared **Comments**/
  **Activity** sidebar (see below).

## Opportunities

`/opportunities` — the pipeline as a six-column board (Lead → Qualified →
Proposal → Negotiation → Won → Lost), scrolling sideways rather than crushing
the columns. Each column heads with a count and its total value; each card
shows the customer, the value, the next action and its due date (red when
late), and flags a missing owner. `/opportunities/<id>` is a standard detail
page: title, customer, value, stage, owner, next action + due date, notes,
archive, where-it-came-from, attachments, comments, activity.

A deal's `last_activity_at` is bumped on every save — that timestamp is what
"stalled" reads.

## Projects

`/projects` — a table with owner, due date, work left ("4 of 11", plus an
overdue-task badge) and status (planning / active / at risk / blocked / done).
`/projects/<id>` adds the project's tasks, its commitments, the notes it came
from, attachments, comments and activity.

## Tasks

`/tasks` — a kanban board grouped by status (**To Do** / **In Progress** /
**Done**), each column headed by a dot + count. **New task** opens a modal:
title (required), customer, owner (a Person), project, status, priority
(low / normal / high / urgent), due date, description. Cards show the
customer, the owner (or "No owner") and the due date, red when late. Cards can
be dragged between columns (plain HTML5 drag-and-drop, no library) to change
status; on drop the whole column's new order is POSTed to `/tasks/reorder`,
which only ever touches `status`/`position`.

Clicking a card opens `/tasks/<id>` — edit form for every field, **Archive**,
where-it-came-from, **Attachments**, and the shared **Comments**/**Activity**
sidebar. Search (`?q=`) matches title or description.

A task has two owner columns on purpose: `owner` is a **Person** (which is
what the forms and dashboards use, and can name someone with no account), and
`assignee` is Core's **User**, kept in sync from the owner's linked account so
reassignment notifications still fire.

## Commitments

`/commitments` — someone said they'd do a specific thing. A filter row (Open /
Overdue / Mine / Done / Everything) over a table of person, commitment,
due date, status, and a one-click **Mark done** / **Reopen**. **Record a
promise** opens a modal with description (required), person, due date,
customer, project and source (by hand / from a meeting / from an email;
Capture sets it to `capture`).

**Overdue is not a stored status.** The model stores open / done / cancelled;
"Overdue" is `open` plus a due date in the past, computed at display time.

## Decisions

`/decisions` — what was decided *and why*, most recent first, searchable
across title, decision and rationale. Each entry shows the decision, the
rationale set apart in the reading face, and a footer with owner, decided
date, review date and any linked project or customer. Anything due for review
inside 14 days is badged, and also surfaces on the dashboard.

## Notes

`/notes` — every captured text, searchable, with its author, date and how many
records came out of it. `/notes/<id>` shows the text verbatim, its tags, what
came out of it (linked records, each labelled by type), and comments/activity.
A note whose proposal hasn't been reviewed yet shows an **Awaiting review**
badge and a link back to the review panel.

## People

`/people` — the directory of who can own work. Name, role, open tasks, open
or overdue commitments, and whether they have a login. **A Person is not a
User**: a User is an account, a Person is a name you can hand a commitment to.
Most are the same human and get linked, but the split is what lets a contact
at a customer be named without giving them access, and what lets the demo seed
eight colleagues without eight fake logins. People are deactivated rather than
deleted, since their name is on commitments and decisions that stay true after
they've left.

## Demo data

Registering the first account seeds a fictional company (`seed.py`): eight
people, fifteen customers, ten opportunities, six projects, forty tasks,
fifteen commitments, ten decisions and twenty notes, plus backdated activity
so "What's changed" isn't empty on the first load.

**Every date is relative to the day you seed**, so the deliberate problems —
overdue commitments, two stalled deals, a blocked project, ten unassigned
tasks, a pricing decision due for review — hold whenever the demo runs. Reseed
a scratch database with `SQLITE_PATH=/tmp/scratch.db python3 seed.py`.

## Comments, attachments & activity

Shared across every record type (`Comment`/`Attachment`/`Activity`/`NoteLink`
are keyed by `subject_type` + `subject_id` rather than having a separate table
per model). `insights.SUBJECT_REGISTRY` is the single table mapping a type to
its model, detail route and label:

- **Comments**: plain text (rendered with `white-space: pre-wrap`, not
  Markdown — only Ask AI's replies get Markdown rendering). Any signed-in
  user can comment; only the comment's own author can delete it.
- **Attachments**: uploaded to local disk under `UPLOAD_FOLDER`
  (default `./uploads/<subject_type>/<subject_id>/`), 10 MB max per file.
  Download streams the file back with its original filename; delete removes
  both the DB row and the file.
- **Activity**: an append-only log (`created` / `updated` / `status_changed` /
  `archived` / `commented` / `attached` / `captured` / `extracted`) shown
  newest-first in the detail page's sidebar, and rendered as sentences in the
  dashboard's **What's changed**. Written by `record_activity()` — see
  [AGENTS.md](AGENTS.md) if you're adding a new mutation that should log one.
- **Note links**: which records came out of which note. Every detail page has
  a **Where it came from** section reading them back, and a note's own page
  lists everything extracted from it.

## Notifications

`/notifications` — a flat, newest-first list. Three kinds today:
**assignment** (you were made a task's assignee), **comment** (someone
commented on something — not currently wired to any route, but the
`notify()` helper and template already handle the kind), and **reminder**
(see [Scheduled jobs & reminders](#scheduled-jobs--reminders)). Unread rows
get a dot marker; "Mark all read" and per-row "Mark read" are both one POST.
There's no unread-count badge in the sidebar nav yet.

## Ask AI (chat assistant)

`/chat` — a single running thread per user (no multi-thread UI; `ChatThread`
is 1:1 with `User`). Two interchangeable backends, picked automatically:

- **OpenAI-compatible cloud API** if `OPENAI_API_KEY` is set
  (`OPENAI_MODEL`, `OPENAI_BASE_URL` configurable).
- **Local Ollama** otherwise (`OLLAMA_HOST`, `OLLAMA_MODEL` configurable) —
  the page shows a "Backend: local Ollama" notice with the env vars to check
  if nothing answers.

It's a tool-calling agent (`ai.py`) over **read** tools — `list_clients`,
`get_client`, `list_tasks`, `get_task`, `list_opportunities`, `list_projects`,
`list_commitments`, `list_decisions`, `list_people`, `search` (which spans
every record type), and `get_attention` — and **write** tools covering every
record type: `create_client`/`update_client`/`archive_client`,
`create_task`/`update_task`/`archive_task`, `create_project`/
`update_project`/`archive_project`, `create_opportunity`/`update_opportunity`/
`archive_opportunity`, `create_commitment`/`update_commitment`,
`create_decision`/`update_decision`, `create_person`/`update_person`,
`create_note`/`update_note`, and `create_reminder` — up to
`MAX_TOOL_ROUNDTRIPS` (6) per message. Commitment, Decision, Person and Note
have no archive tool: none of them has an `archived_at` column, so retiring
one is a `status`/`active` update instead (same as their pages/*.py routes,
where one exists at all — Decision and Note have no manual create/edit UI
yet, only Capture's extraction path; the chat tools are these two types'
only hand-editing surface today). `create_reminder` is the same shape:
no manual UI at all, see [Scheduled jobs & reminders](#scheduled-jobs--reminders).
Read tools execute immediately; a write tool call **pauses the turn** and
shows a confirmation banner ("The assistant wants to: …") with Confirm/
Cancel buttons before anything is actually written — `ChatThread.pending_*`
persists the paused state across requests so refreshing the page doesn't
lose it. Confirming re-enters the same tool loop with the write executed
(and its own activity/notification side effects, same as doing it by hand);
cancelling drops it and tells the model so.

`get_attention` returns the dashboard and control center *as data*, by calling
the same `insights.py` functions those pages render. It exists so the
assistant's answer to "what needs my attention?" cannot contradict the screen
the user is looking at.

Model replies are rendered through a small hand-rolled Markdown-to-HTML
renderer (`ai.py: render_markdown`) — the only place in the app that
happens; everything else (comments, descriptions, notes) is plain text.

## MCP server

`mcp_server.py` — a standalone [MCP](https://modelcontextprotocol.io) server,
hand-rolled JSON-RPC 2.0 over stdio (no `mcp` pip package, per the
zero-dependency rule), so an MCP client (Claude Desktop, Claude Code, ...)
can call Pro's data directly instead of through the `/chat` UI. Run as a
subprocess, not as part of the web app:

```json
{ "mcpServers": { "scalar-pro": {
    "command": "python3", "args": ["/absolute/path/to/pro/mcp_server.py"]
} } }
```

It exposes the exact same tool set as Ask AI — `ai.TOOLS_SCHEMA` becomes
`tools/list`, `ai._execute_tool` is `tools/call`'s dispatcher — so anything
listed under [Ask AI](#ask-ai-chat-assistant) above is reachable here too,
read tools and every write tool alike.

Two differences from the chat path, both consequences of there being no
browser session on a stdio pipe:

- **No confirmation pause.** The chat UI stops on a write tool for a
  Confirm/Cancel click. MCP hosts already show their own "allow this tool
  call?" prompt before ever sending `tools/call`, so that's this
  transport's confirmation — writes run immediately once called.
- **No signed-in user to act as.** Writes are attributed to the first
  registered account (the workspace owner), the same convention
  `seed.py`'s own standalone entrypoint uses.

No new configuration — it reads the same `.env` (in particular `SQLITE_PATH`,
so it talks to the same database) as `app.py`.

## Scheduled jobs & reminders

`jobs.py` runs a single background thread (started from `app.py` at process
startup) that polls the database every 30 seconds and runs whichever
registered jobs are due — a small stdlib-only (`threading` + `time`)
scheduler, not a task queue.

The one job today is firing **reminders**: a `Reminder` (message, `remind_at`,
optionally linked to any record type — client/task/person/project/
opportunity/commitment/decision/note) is created only via Ask AI/MCP's
`create_reminder` tool — "remind me about this project in 2 days" or "remind
me about this task in 10 minutes" — there's no manual form for it. Once
`remind_at` passes, the job creates a **reminder** notification (see
[Notifications](#notifications)) for whoever asked, emails them the reminder
text, and logs a `reminder_fired` activity entry if it was linked to a
record.

A fired reminder pops up as a toast wherever the person is in the app, not
just on their next click: a small polling script in `layout.html` hits
`GET /notifications/poll` every 20 seconds and shows whatever comes back via
the same `ot.toast()` used for flash messages. A page that's already open
gets the toast within that window; `pages/notifications.py`'s
`_toast_due_reminders` `before_request` hook is the no-JS/first-load
fallback for everyone else. Either path marks the reminder's notification
read — for a reminder, seeing the toast *is* the read receipt, unlike
assignment/comment notifications, which still wait for an explicit "mark
read" on the /notifications page. There's no page to browse or cancel a
reminder before it fires.

## Settings & appearance

`/settings`:

- **Appearance** — a Dark/Light toggle. See
  [Interface & behavior](#interface--behavior) for how it's implemented.
- **Change password** (or **Set a password**, if the account somehow has
  none — the form always asks for the current password since there's no
  alternate sign-in method here to fall back on) — collapsed behind a
  `<details>`/`<summary>` disclosure. A failed or successful submit
  redirects to `#change-password` so the disclosure re-opens instead of
  hiding its own result.
- **Team** — every member's name/email/role, plus (owner/admin only) an
  invite form and a list of pending invite emails, laid out side by side
  (same `.detail-cols` two-column grid as the client/task detail pages;
  stacks on narrow screens).
- **Environment** (`/settings/env`, owner only) — a form for this instance's
  `.env` file: API keys, email sending, dev-server settings, etc. The form
  is generated from `.env.example` itself (section headers + one commented
  `# KEY=example` line per setting are parsed into fields; no separate list
  to keep in sync), pre-filled from `os.environ`. Saving rewrites `.env`,
  commenting a field back out if it's left blank, and updates `os.environ`
  for the current process — though settings read once at import time
  (ports, model names, file paths) still need a restart to take effect.
  Key-/secret-/token-/password-shaped vars render as masked fields with a
  Show/Hide toggle. The first owner is sent here (`?onboarding=1`, adds a
  "Skip for now" link to the dashboard) right after `/register` — everyone
  else reaches it from the Settings page.

## Error pages

`app.py`'s 404/403/500 handlers all render the same `error.html`, styled as
a small card (icon + monospace `ERR_<code>` tag, heading, message, a
divider, then actions) instead of a bare heading and link. The icon and its
tinted badge vary by code — a compass for 404, a padlock for 403, an alert
triangle for 500 — reusing this app's existing status-color tokens
(muted/warning/danger) rather than introducing new colors. A **Go home**
button is always shown; 500 also gets a **Try again** button that reloads
the same URL. Deliberately dependency-free (no icon library, no JS): the
500 handler already rolls back the DB and guards the render itself against
a second crash (see its comment in `app.py`), so the page most likely to
render right after something broke shouldn't lean on anything else that
could fail with it.

## Navigation & keyboard

- **Esc** goes "back" a level: a detail page (`/clients/<id>`, `/tasks/<id>`,
  `/projects/<id>`, `/opportunities/<id>`, `/notes/<id>`) goes to its list; a
  list page, and `/control`, go to the dashboard. Opt-in per page via
  `<body data-esc-back>`; pages that don't set it (dashboard, chat, settings,
  notifications) aren't affected.
  Suppressed while a modal dialog is open or while typing in a field.
- **Mobile nav**: below 860px, the sidebar becomes an off-canvas drawer
  (hamburger button, backdrop, slide-in) — pure CSS (`:checked` on a hidden
  checkbox), no JS.

## Email

Resend's HTTP API (`utils.py: Mailer`), used for invite, password-reset,
and reminder emails. Without `RESEND_API_KEY` set, sending raises
`MailerError`; the invite/reset call sites catch that and fall back to
putting the link directly in a flash message ("Share this link instead: …")
so the flow still works without email configured — handy for local dev.
`jobs.py`'s reminder job instead just logs the failure and moves on (there's
no request/flash to fall back to from a background thread) — the in-app
notification still goes out either way.

## Interface & behavior

- **Theme**: dark by default; the Settings toggle writes
  `localStorage['scalar-pro:theme']` and sets `document.documentElement
  .style.colorScheme`. `style.css`'s `:root` tokens are all defined with CSS
  `light-dark()`, which resolves off that `color-scheme` value — flipping it
  is the entire retheme, no second `[data-theme]` override block to keep in
  sync. A tiny inline script in `layout.html`'s `<head>` applies the saved
  preference before the stylesheets load, so there's no flash of the wrong
  theme. Light mode isn't a flat inversion of dark: the brand indigo
  (`--primary`) stays constant, but the pale accent colors
  (`--danger`/`--success`/`--warning`) get deepened for legibility as text
  on a white background, and each one's `-foreground` pairing (used for
  solid-fill buttons like **Archive**) flips alongside it.
- **Installable**: a manifest (`static/manifest.json`) plus favicon/
  apple-touch-icon links and `theme-color`/`apple-mobile-web-app-*` meta
  tags in `layout.html`'s `<head>` make this installable as a standalone app
  from a browser's "Install"/"Add to Home Screen" prompt — no service
  worker, just enough for the OS to treat it as an app icon. Same brand mark
  as admin's own (`admin/static/favicon.svg` + `icon-*.png`, regenerated via
  `admin/scripts/make_icons.py`), copied in rather than shared since each
  app serves its own `static/`.
- **Toasts**: every flashed message (`utils.py: flash()`) renders as an
  `oat.toast()` popup (top-right, colored by category) instead of an inline
  banner — see `templates/_toasts.html`. Embeds the message as a JS string
  literal via `app.js_string()`, which escapes `</script>`-breakout
  characters, since some messages interpolate user-supplied text (a
  filename, a task title).
- **Modals**: New Client/New Task open a native `<dialog>` (oat.css themes
  it as a centered card with a dimmed backdrop) via the declarative
  `commandfor`/`command="show-modal"` attributes — `oat.min.js` wires that
  up (using the browser's native support when present, its own click
  handler otherwise), so there's still no app-authored interaction JS for
  it.
- **Sidebar**: sticky + viewport-height, so the user name/logout footer at its
  bottom stays in view regardless of how tall the current page's content is.
  Thirteen links is too many to scan as one list, so they're grouped under
  mono-caps headings by what you'd be doing — Today, Pipeline, Work, Record,
  More.
- **The ledger**: the dashboard's attention list, all four ledger sections of
  `/control`, and the Capture review are one shared partial. A mono left gutter
  carries elapsed time (`6d over`, `14d quiet`, `today`), tinted red for
  overdue and amber for stalled; a hairline separates rows; there's no card
  around any of it. The gutter is the point — on both pages the number that
  matters is how long something has been wrong — and reusing it is what makes
  the CEO's six lines read as a slice of the COO's full list.
- **Type**: three roles, all system stacks so the app stays offline-capable
  with no webfont. `--font-serif` is the briefing voice (dashboard and control
  center headlines, the ledger's primary line, the Capture prompt, a note's
  body, a decision's rationale); the sans stack is all chrome; the mono stack
  is the gutter, the snapshot labels, the nav sections and the record-type
  tags.
- **Motion**: exactly one orchestrated moment — the attention ledger's rows
  fade and rise in sequence on load, so the briefing arrives rather than
  appearing. Nothing else animates, and the whole thing sits behind
  `prefers-reduced-motion`.
- **View transitions**: `@view-transition { navigation: auto }` opts every
  same-origin navigation into the browser's cross-document transition
  (Chromium only; a no-op elsewhere). The default root crossfade is
  disabled (`::view-transition-old/new(root) { animation: none }`) — its
  `mix-blend-mode: plus-lighter` blend flashes white on a dark theme
  whenever the two page snapshots don't match exactly.
- **Chat layout**: the Ask AI page is a `flex-direction: column` page
  (`.chat-page`) sized to the viewport minus the shell's own padding, with
  the message log as the one `flex: 1` scrolling region — not a fixed `vh`
  guess, so it adapts to whatever else is on the page (the Ollama notice,
  the pending-confirmation banner) instead of clipping or leaving dead
  space.

## Data model reference

All models in `models.py`; schema changes are applied idempotently at
startup by `ensure_schema()` (see [AGENTS.md](AGENTS.md) — there's no
migrations directory).

| Model | Purpose |
|---|---|
| `User` | Account: email, password hash, name. |
| `TeamMember` | 1:1 with `User`. `role`: owner / admin / member. |
| `Invite` | Pending join invite: email, token, role, accepted flag. |
| `PasswordReset` | Single-use, time-limited reset token. |
| `Client` | The customer. name, email, phone, company, status, notes, soft-delete via `archived_at`. |
| `Person` | Who can own work. name, role, email, `active`, optional `user` link. Not the same as an account. |
| `Project` | name, description, status, `owner` (Person), `customer`, `due_date`, `last_activity_at`, soft-delete. |
| `Task` | title, description, status, priority, `due_date`, `owner` (Person), `assignee` (User, kept in sync), `client`, `project`, `position`, soft-delete. |
| `Opportunity` | A deal. title, `customer`, `value` (whole units), stage, `owner`, `next_action` + due, notes, `last_activity_at`, soft-delete. |
| `Commitment` | Someone promised something. description, `person`, `due_date`, status (open/done/cancelled — **never** overdue), source, `customer`, `project`. |
| `Decision` | title, decision, **rationale**, `owner`, `decided_on`, `review_on`, status, `customer`, `project`. |
| `Note` | The raw text layer. title, body, `author`, `occurred_on`, tags, `proposal_json` (extraction awaiting review), `captured`. |
| `NoteLink` | Which records came out of which note. Generic over `subject_type`+`subject_id`. |
| `Comment` | Generic over `subject_type`+`subject_id` (client/task). Plain text. |
| `Attachment` | Generic over subject. Local-disk file, metadata in DB. |
| `Activity` | Generic over subject. Append-only log: verb + JSON payload. |
| `Notification` | Per-user. `kind` + JSON payload, `read_at`. |
| `Reminder` | message, `remind_at`, optional generic subject link, `sent_at` (null until `jobs.py` fires it). Created only via Ask AI/MCP. |
| `ChatThread` | 1:1 with `User`. Holds `pending_*` fields for an in-flight write confirmation. |
| `ChatMessage` | user/assistant turns in a thread. |

## Configuration reference

See `.env.example` for the full list with defaults; the short version. All
of these can also be edited from the app itself at `/settings/env` (owner
only) — see [Settings & appearance](#settings--appearance).

| Var | Purpose |
|---|---|
| `SECRET_KEY` | Signs the session cookie. Set a real value in production. |
| `SQLITE_PATH` | DB file location (default `app.db` next to `models.py`). |
| `UPLOAD_FOLDER` | Attachment storage root (default `./uploads`). |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` | Ask AI **and Capture**, cloud backend. |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | Ask AI **and Capture**, local backend (used when `OPENAI_API_KEY` is unset). |
| `RESEND_API_KEY` / `RESEND_FROM` | Invite/reset emails. Unset = flash a shareable link instead. |
| `HOST` / `PORT` / `DEBUG` | Dev server (see `Makefile`/`app.py`). |
