"""Peewee models for Binders Pro.

Single-tenant: there is no Workspace concept at all — one instance == one
customer. Schema changes ship as idempotent "does this column exist yet"
checks run from ensure_schema() at startup (see the bottom of this file) —
there is no migrations/ directory and no peewee-migrate dependency, per the
zero-pip-dependency constraint (peewee + bottle only).

Pro keeps Core's whole model set unchanged and adds the seven records a
company actually runs on: Person, Project, Opportunity, Commitment, Decision,
Note and NoteLink. Every dashboard in this app is a query over those plus
Client/Task — nothing is precomputed and no "is_overdue" flag is stored, so a
demo seeded three months ago still reads correctly today (see insights.py).
"""

from __future__ import annotations

import datetime
import os

from peewee import (
    AutoField,
    BooleanField,
    CharField,
    DatabaseProxy,
    DateField,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

# The concrete database is chosen at startup (see make_database/init_database)
# so the choice can be deferred until after .env is loaded (app.py does this).
db = DatabaseProxy()


def make_database() -> SqliteDatabase:
    """Build the concrete database. Local SQLite file, path overridable via
    SQLITE_PATH (defaults to app.db next to this file)."""
    sqlite_path = os.environ.get(
        "SQLITE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")
    )
    return SqliteDatabase(
        sqlite_path, pragmas={"journal_mode": "wal", "foreign_keys": 1}
    )


def init_database() -> SqliteDatabase:
    """Bind the proxy to the concrete backend. Idempotent."""
    if db.obj is None:
        db.initialize(make_database())
    return db.obj


class BaseModel(Model):
    class Meta:
        database = db


# ---------------------------------------------------------------------------
# Identity — no workspace, no "client" access tier. Whoever has an account
# has full access; TeamMember.role is informational (owner|admin|member).
# ---------------------------------------------------------------------------


class User(BaseModel):
    id = AutoField()
    email = CharField(unique=True)
    password_hash = CharField()
    name = CharField()
    created_at = DateTimeField(default=datetime.datetime.now)


class TeamMember(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="membership", unique=True, on_delete="CASCADE")
    role = CharField(default="member")  # owner | admin | member
    created_at = DateTimeField(default=datetime.datetime.now)


class Invite(BaseModel):
    """A pending "join the team" invite, emailed as a tokenized accept link."""

    id = AutoField()
    email = CharField()
    token = CharField(unique=True)
    role = CharField(default="member")
    invited_by = ForeignKeyField(User, backref="sent_invites", on_delete="CASCADE")
    accepted = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.now)


class PasswordReset(BaseModel):
    """Single-use, time-limited token for resetting a user's password."""

    id = AutoField()
    user = ForeignKeyField(User, backref="password_resets", on_delete="CASCADE")
    token = CharField(unique=True)
    expires_at = DateTimeField()
    used = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.now)


# ---------------------------------------------------------------------------
# Core typed models — real columns, no generic FieldDef/FieldValue.
# ---------------------------------------------------------------------------


class Client(BaseModel):
    id = AutoField()
    name = CharField()
    email = CharField(default="")
    phone = CharField(default="")
    company = CharField(default="")
    status = CharField(default="lead")  # lead | active | inactive
    notes = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    created_by = ForeignKeyField(User, backref="created_clients", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)

    class Meta:
        database = db
        indexes = ((("status",), False),)


class Person(BaseModel):
    """The people directory — who can own work.

    Deliberately NOT the same thing as User. A User is an account (email +
    password + session); a Person is a name you can hand a commitment to.
    Most are the same human and get linked via `user`, but the split is what
    lets the demo seed eight colleagues without eight fake logins, and what
    lets a contact at a customer be named as the person who promised
    something without giving them access to the app."""

    id = AutoField()
    name = CharField()
    role = CharField(default="")  # freeform job title, e.g. "Head of Sales"
    email = CharField(default="")
    active = BooleanField(default=True)
    user = ForeignKeyField(User, backref="person", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)


class Project(BaseModel):
    id = AutoField()
    name = CharField()
    description = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    status = CharField(default="planning")  # planning | active | at_risk | blocked | done
    owner = ForeignKeyField(Person, backref="owned_projects", null=True, on_delete="SET NULL")
    customer = ForeignKeyField(Client, backref="projects", null=True, on_delete="SET NULL")
    due_date = DateField(null=True)
    # Bumped on every write (see touch()). "Stalled" is `now - last_activity_at`
    # rather than a stored flag, so it stays true without a nightly job.
    last_activity_at = DateTimeField(default=datetime.datetime.now)
    created_by = ForeignKeyField(User, backref="created_projects", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)

    class Meta:
        database = db
        indexes = ((("status",), False),)


class Task(BaseModel):
    id = AutoField()
    title = CharField()
    description = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    status = CharField(default="todo")  # todo | in_progress | done
    # Two owner columns on purpose. `owner` (a Person) is what Pro's forms and
    # dashboards use — it's the one that can point at someone without an
    # account. `assignee` (a User) is Core's, and it's what the notification
    # path keys off; task_update keeps it in sync from owner.user so
    # reassignment notifications still fire. Don't collapse them without also
    # reworking notify() and the Core-compatible AI write tools.
    owner = ForeignKeyField(Person, backref="owned_tasks", null=True, on_delete="SET NULL")
    assignee = ForeignKeyField(User, backref="assigned_tasks", null=True, on_delete="SET NULL")
    client = ForeignKeyField(Client, backref="tasks", null=True, on_delete="SET NULL")
    project = ForeignKeyField(Project, backref="tasks", null=True, on_delete="SET NULL")
    due_date = DateField(null=True)
    priority = CharField(default="normal")  # low | normal | high | urgent
    position = IntegerField(default=0)
    created_by = ForeignKeyField(User, backref="created_tasks", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)

    class Meta:
        database = db
        indexes = (
            (("status", "position"), False),
            (("client",), False),
            (("due_date",), False),
        )


class Opportunity(BaseModel):
    """A deal in the pipeline. `value` is whole currency units (no cents) —
    this is a sales pipeline, not an invoicing ledger, and rounding to the
    dollar keeps the seed data and the extracted "$50k" readable."""

    id = AutoField()
    title = CharField()
    customer = ForeignKeyField(Client, backref="opportunities", null=True, on_delete="SET NULL")
    value = IntegerField(default=0)
    stage = CharField(default="lead")  # lead | qualified | proposal | negotiation | won | lost
    owner = ForeignKeyField(Person, backref="owned_opportunities", null=True, on_delete="SET NULL")
    next_action = CharField(default="")
    next_action_due = DateField(null=True)
    notes = TextField(default="")  # plain text — rendered with white-space: pre-wrap
    last_activity_at = DateTimeField(default=datetime.datetime.now)
    created_by = ForeignKeyField(User, backref="created_opportunities", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    archived_at = DateTimeField(null=True)

    class Meta:
        database = db
        indexes = ((("stage",), False),)


class Commitment(BaseModel):
    """Someone said they'd do a thing. A task with a promise attached.

    Note what's missing: an "overdue" status. The spec lists one, but storing
    it would mean a nightly job and a demo that rots. Overdue is
    `due_date < today and status == "open"`, computed at read time in
    insights.py."""

    id = AutoField()
    description = TextField()
    person = ForeignKeyField(Person, backref="commitments", null=True, on_delete="SET NULL")
    due_date = DateField(null=True)
    status = CharField(default="open")  # open | done | cancelled
    source = CharField(default="manual")  # manual | meeting | email | capture
    customer = ForeignKeyField(Client, backref="commitments", null=True, on_delete="SET NULL")
    project = ForeignKeyField(Project, backref="commitments", null=True, on_delete="SET NULL")
    created_by = ForeignKeyField(User, backref="created_commitments", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = (
            (("status", "due_date"), False),
            (("person",), False),
        )


class Decision(BaseModel):
    """What we decided, and why. `rationale` is the reason the whole model
    exists — a decision without its "why" is just a status change."""

    id = AutoField()
    title = CharField()
    decision = TextField(default="")
    rationale = TextField(default="")
    owner = ForeignKeyField(Person, backref="owned_decisions", null=True, on_delete="SET NULL")
    decided_on = DateField(null=True)
    review_on = DateField(null=True)
    status = CharField(default="decided")  # decided | under_review | superseded
    customer = ForeignKeyField(Client, backref="decisions", null=True, on_delete="SET NULL")
    project = ForeignKeyField(Project, backref="decisions", null=True, on_delete="SET NULL")
    created_by = ForeignKeyField(User, backref="created_decisions", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)


class Note(BaseModel):
    """The raw text layer, and the thing Capture writes first.

    Structured records don't replace the text they came from — the note is
    kept verbatim and every record extracted from it points back here via
    NoteLink. `proposal_json` holds the extraction awaiting review, which is
    why a half-finished Capture survives a page refresh: the draft lives on
    the row, not in the session."""

    id = AutoField()
    title = CharField(default="")
    body = TextField()
    author = ForeignKeyField(Person, backref="notes", null=True, on_delete="SET NULL")
    occurred_on = DateField(null=True)
    tags = CharField(default="")  # comma-separated, freeform
    proposal_json = TextField(default="")  # AI extraction awaiting review; "" once resolved
    captured = BooleanField(default=False)  # True once its proposal has been accepted or discarded
    created_by = ForeignKeyField(User, backref="created_notes", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)


class NoteLink(BaseModel):
    """Which records came out of which note. Generic over subject the same way
    Comment/Attachment/Activity are (subject_type + subject_id), so linking a
    new record type needs no schema change at all."""

    id = AutoField()
    note = ForeignKeyField(Note, backref="links", on_delete="CASCADE")
    subject_type = CharField()
    subject_id = IntegerField()
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = (
            (("note",), False),
            (("subject_type", "subject_id"), False),
        )


# ---------------------------------------------------------------------------
# Generic-over-subject helpers (Comment/Attachment/Activity apply to either a
# Client or a Task, keyed by subject_type + subject_id rather than a link
# table per model — one implementation instead of two).
# ---------------------------------------------------------------------------

SUBJECT_TYPES = (
    "client", "task", "person", "project",
    "opportunity", "commitment", "decision", "note",
)

# Shared with pages.py (form choices) and ai.py (tool-schema enums / write-tool
# validation) — defined once here so ai.py can import them without importing
# pages.py back (pages.py does `import ai`, so that direction would cycle).
CLIENT_STATUSES = ("lead", "active", "inactive")
TASK_STATUSES = ("todo", "in_progress", "done")
PROJECT_STATUSES = ("planning", "active", "at_risk", "blocked", "done")
OPPORTUNITY_STAGES = ("lead", "qualified", "proposal", "negotiation", "won", "lost")
# Stages a deal can still move out of — everything the pipeline total, the
# stalled check and the "open opportunities" count care about.
OPPORTUNITY_OPEN_STAGES = ("lead", "qualified", "proposal", "negotiation")
COMMITMENT_STATUSES = ("open", "done", "cancelled")
DECISION_STATUSES = ("decided", "under_review", "superseded")
TASK_PRIORITIES = ("low", "normal", "high", "urgent")

_STATUS_LABELS = {
    "lead": "Lead", "active": "Active", "inactive": "Inactive",
    "todo": "To Do", "in_progress": "In Progress", "done": "Done",
    "at_risk": "At Risk", "under_review": "Under Review",
}


def status_label(status: str) -> str:
    """Human-readable form of a Client/Task status value for display —
    e.g. "in_progress" -> "In Progress". The raw value (still what's
    stored, matched in queries, and submitted by forms/tool calls) is
    untouched; only what's shown to a person goes through this. Falls back
    to a generic underscore->title-case conversion for any status not in
    the table above, so a future status doesn't need this list updated."""
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


class Comment(BaseModel):
    id = AutoField()
    subject_type = CharField()  # "client" | "task"
    subject_id = IntegerField()
    body = TextField()  # plain text — rendered with white-space: pre-wrap
    author = ForeignKeyField(User, backref="comments", on_delete="CASCADE")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id"), False),)


class Attachment(BaseModel):
    id = AutoField()
    subject_type = CharField()
    subject_id = IntegerField()
    filename = CharField()
    stored_path = CharField()  # relative path under UPLOAD_FOLDER
    mime = CharField(default="application/octet-stream")
    size = IntegerField(default=0)
    uploaded_by = ForeignKeyField(User, backref="uploads", null=True, on_delete="SET NULL")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id"), False),)


class Activity(BaseModel):
    id = AutoField()
    subject_type = CharField()
    subject_id = IntegerField()
    actor = ForeignKeyField(User, backref="activity", null=True, on_delete="SET NULL")
    verb = CharField()  # created | updated | status_changed | commented | archived | ...
    payload_json = TextField(default="{}")
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = ((("subject_type", "subject_id", "created_at"), False),)


class Notification(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="notifications", on_delete="CASCADE")
    kind = CharField()  # assignment | comment | ...
    payload_json = TextField(default="{}")
    read_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        database = db
        indexes = (
            (("user", "read_at"), False),
            (("user", "created_at"), False),
        )


# ---------------------------------------------------------------------------
# Ask-AI chat — one running thread per user, kept simple (no multi-thread UI).
# ---------------------------------------------------------------------------


class ChatThread(BaseModel):
    id = AutoField()
    user = ForeignKeyField(User, backref="chat_threads", on_delete="CASCADE")
    created_at = DateTimeField(default=datetime.datetime.now)
    # Set together when the AI proposes a write and it's paused for human
    # confirmation (see ai.py: _save_pending/_clear_pending/resolve_pending);
    # null the rest of the time.
    pending_tool_calls = TextField(null=True)  # JSON: raw tool_call dicts awaiting confirmation
    pending_convo = TextField(null=True)  # JSON: full in-flight message list, incl. the proposing assistant turn
    pending_round_idx = IntegerField(null=True)  # _agent_loop's round counter, so resume doesn't reset the budget


class ChatMessage(BaseModel):
    id = AutoField()
    thread = ForeignKeyField(ChatThread, backref="messages", on_delete="CASCADE")
    role = CharField()  # user | assistant
    content = TextField()
    created_at = DateTimeField(default=datetime.datetime.now)


# ---------------------------------------------------------------------------
# Schema — idempotent create + column checks, run at startup (no migrations/
# directory, no peewee-migrate; see the Context note in the project plan).
# ---------------------------------------------------------------------------

ALL_MODELS = [
    User,
    TeamMember,
    Invite,
    PasswordReset,
    Client,
    Person,
    Project,
    Task,
    Opportunity,
    Commitment,
    Decision,
    Note,
    NoteLink,
    Comment,
    Attachment,
    Activity,
    Notification,
    ChatThread,
    ChatMessage,
]


def _column_exists(table: str, column: str) -> bool:
    cur = db.execute_sql(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    """`ddl` is the full column definition, e.g. "VARCHAR(255) DEFAULT ''".
    Called from ensure_schema() for every future non-destructive column add —
    same pattern an AI edit would append here (one model field + one line)."""
    if not _column_exists(table, column):
        db.execute_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_schema() -> None:
    """Create any missing tables/columns/indexes. Safe to call on every
    startup. This is the entire "migrations" story for this app."""
    init_database()
    db.create_tables(ALL_MODELS, safe=True)
    # Columns added after the initial schema, one line per field added above:
    _add_column_if_missing("chatthread", "pending_tool_calls", "TEXT")
    _add_column_if_missing("chatthread", "pending_convo", "TEXT")
    _add_column_if_missing("chatthread", "pending_round_idx", "INTEGER")
    # Pro's additions to Core's task table. Plain ALTERs with no FK constraint:
    # SQLite can't add a REFERENCES column to an existing table, and peewee
    # resolves these through the model definition anyway.
    _add_column_if_missing("task", "owner_id", "INTEGER")
    _add_column_if_missing("task", "project_id", "INTEGER")
    _add_column_if_missing("task", "due_date", "DATE")
    _add_column_if_missing("task", "priority", "VARCHAR(255) DEFAULT 'normal'")
