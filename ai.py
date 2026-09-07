"""Everything this app asks a model to do.

Three jobs, and deliberately only three:

1. **Explain** — a tool-calling chat agent with read tools over every record
   type, plus `get_attention`, which returns the dashboard and control center
   as data so the assistant's answer can't contradict the page the user is
   looking at. Writes are still limited to the six Client/Task tools, each of
   which pauses for human confirmation.
2. **Extract** — `complete_json()`, the JSON-mode call behind Capture. See
   modules/capture/extract.py for the prompt, the allow-list and everything
   that happens to the result before it's allowed near the database.
3. **Summarize** — no separate code path: it's the chat agent answering
   "what needs my attention?" through the tools above. The dashboards
   themselves are computed in Python (insights.py), never generated, because
   a 30-second morning read can't wait on an API call and must not invent a
   number.

Two interchangeable backends (matches admin/services/ai.py's shape):
  - OpenAI-compatible cloud API, used when OPENAI_API_KEY is set
    (OPENAI_MODEL, OPENAI_BASE_URL configurable).
  - Local Ollama, used otherwise (OLLAMA_HOST, OLLAMA_MODEL configurable).

Both speak stdlib urllib.request + json — no HTTP client dependency, and no
certifi: plain ssl.create_default_context() works fine against api.openai.com
on Linux via the system CA bundle.

Markdown is rendered ONLY here. Task/Client descriptions and comments
elsewhere in the app are plain text (CSS white-space: pre-wrap) — this is a
one-directional, display-only render of model output, never round-tripped
back into Markdown, so a ~65-line hand-rolled renderer is enough; no need for
a real Markdown library.
"""

from __future__ import annotations

import datetime
import html as _html
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any

from models import (
    CLIENT_STATUSES,
    COMMITMENT_STATUSES,
    OPPORTUNITY_OPEN_STAGES,
    OPPORTUNITY_STAGES,
    PROJECT_STATUSES,
    TASK_STATUSES,
    ChatMessage,
    ChatThread,
    Client,
    Commitment,
    Decision,
    Note,
    Opportunity,
    Person,
    Project,
    Task,
    User,
)
from utils import notify, record_activity
import insights

REQUEST_TIMEOUT = 120
MAX_TOOL_ROUNDTRIPS = 6
HISTORY_MESSAGES = 12

_SSL_CTX = ssl.create_default_context()


def _openai_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY") or None


def backend() -> str:
    return "openai" if _openai_key() else "ollama"


OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4")

_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def _model_uses_reasoning_effort(model: str) -> bool:
    return model.startswith(_REASONING_MODEL_PREFIXES)


class LLMError(RuntimeError):
    """Raised when either backend fails. The route surfaces the message."""


def health() -> dict[str, Any]:
    if _openai_key():
        return {"backend": "openai", "configured": True, "model": OPENAI_MODEL, "host": OPENAI_BASE_URL}
    try:
        req = urllib.request.Request(OLLAMA_HOST + "/api/tags")
        with urllib.request.urlopen(req, timeout=2) as r:
            json.loads(r.read())
        ok = True
    except Exception:
        ok = False
    return {"backend": "ollama", "configured": ok, "model": OLLAMA_MODEL, "host": OLLAMA_HOST}


# ---------------------------------------------------------------------------
# Tools — read-only: list/get/search over Client and Task.
# ---------------------------------------------------------------------------

TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_clients",
            "description": "List clients, optionally filtered by status (lead/active/inactive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_client",
            "description": "Fetch one client's full details plus their open tasks, by id.",
            "parameters": {
                "type": "object",
                "properties": {"client_id": {"type": "integer"}},
                "required": ["client_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks, optionally filtered by status (todo/in_progress/done) and/or client id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "client_id": {"type": "integer"},
                    "limit": {"type": "integer", "description": "Default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Fetch one task's full details by id.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Keyword search across every record type — customers, tasks, projects, "
                "opportunities, commitments, decisions, people and notes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default 10, max 30."},
                },
                "required": ["query"],
            },
        },
    },
    # --- Pro's records. All read-only: the assistant explains the business,
    # it doesn't restructure it. Writing is still limited to the Client/Task
    # tools below, which pause for confirmation.
    {
        "type": "function",
        "function": {
            "name": "list_opportunities",
            "description": (
                "Deals in the pipeline, biggest first. Defaults to open stages only; pass "
                "stage=won or stage=lost to see closed ones."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "enum": list(OPPORTUNITY_STAGES)},
                    "stalled_days": {
                        "type": "integer",
                        "description": "Only deals with no activity for at least this many days.",
                    },
                    "limit": {"type": "integer", "description": "Default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "Projects, soonest deadline first, each with its open/overdue task counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": list(PROJECT_STATUSES)},
                    "limit": {"type": "integer", "description": "Default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_commitments",
            "description": (
                "Who promised what. Use overdue_only=true for the ones that have slipped, or "
                "person to answer \"what did João promise?\"."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": list(COMMITMENT_STATUSES)},
                    "person": {"type": "string", "description": "Person's name, or part of it."},
                    "overdue_only": {"type": "boolean"},
                    "limit": {"type": "integer", "description": "Default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_decisions",
            "description": (
                "What was decided and why, most recent first. Use this for \"what did we decide "
                "about X\" — the rationale is stored, not just the outcome."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Match against title, decision or rationale."},
                    "limit": {"type": "integer", "description": "Default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_people",
            "description": "The people directory, with how many open tasks and commitments each is carrying.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_attention",
            "description": (
                "The dashboard and control center as data: what needs attention, the five "
                "headline numbers, and what's overdue, at risk, stalled or unassigned. Use this "
                "first for broad questions like \"what needs my attention?\" or \"what's falling "
                "through the cracks?\" — it's the same computation the pages themselves run, so "
                "the answer can't contradict what the user is looking at."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_client",
            "description": "Create a new client. Requires human confirmation before it takes effect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "company": {"type": "string"},
                    "status": {"type": "string", "enum": list(CLIENT_STATUSES)},
                    "notes": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_client",
            "description": (
                "Update one or more fields on an existing client. Only pass fields you intend "
                "to change — omitted fields are left as-is. Requires human confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "company": {"type": "string"},
                    "status": {"type": "string", "enum": list(CLIENT_STATUSES)},
                    "notes": {"type": "string"},
                },
                "required": ["client_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_client",
            "description": "Archive (soft-delete) a client by id. No hard delete, no unarchive. Requires human confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"client_id": {"type": "integer"}},
                "required": ["client_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Create a new task. Optionally link a client and/or assign a team member by user "
                "id (look ids up via list_clients/get_client/search first). Requires human confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string", "enum": list(TASK_STATUSES)},
                    "client_id": {"type": "integer", "description": "Optional; omit for no linked client."},
                    "assignee_id": {"type": "integer", "description": "Optional; omit for unassigned."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Update one or more fields on an existing task. Only pass fields you intend to "
                "change. Pass client_id=0 to unlink the client, or assignee_id=0 to unassign. "
                "Requires human confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string", "enum": list(TASK_STATUSES)},
                    "client_id": {"type": "integer"},
                    "assignee_id": {"type": "integer"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_task",
            "description": "Archive (soft-delete) a task by id. No hard delete, no unarchive. Requires human confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
    },
]

# Mutating tools never execute immediately — _agent_loop pauses on these and
# hands control back to pages.py/chat.html for a human Confirm/Cancel before
# _execute_tool ever actually runs one. Read tools (above) keep running the
# moment the model calls them, exactly as before.
_MUTATING_TOOLS = frozenset({
    "create_client", "update_client", "archive_client",
    "create_task", "update_task", "archive_task",
})


def _client_row(c: Client) -> dict:
    return {
        "id": c.id, "name": c.name, "email": c.email, "phone": c.phone,
        "company": c.company, "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _task_row(t: Task) -> dict:
    return {
        "id": t.id, "title": t.title, "status": t.status,
        "client_id": t.client_id, "client_name": t.client.name if t.client_id else None,
        "assignee": t.assignee.name if t.assignee_id else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _tool_list_clients(*, status: str | None = None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    q = Client.select().where(Client.archived_at.is_null(True))
    if status:
        q = q.where(Client.status == status)
    rows = list(q.order_by(Client.created_at.desc()).limit(limit))
    return {"count": len(rows), "clients": [_client_row(c) for c in rows]}


def _tool_get_client(*, client_id: int) -> dict:
    try:
        c = Client.get_by_id(client_id)
    except Client.DoesNotExist:
        return {"error": f"No client #{client_id}."}
    tasks = list(Task.select().where((Task.client == c) & (Task.archived_at.is_null(True))))
    return {**_client_row(c), "notes": c.notes, "tasks": [_task_row(t) for t in tasks]}


def _tool_list_tasks(*, status: str | None = None, client_id: int | None = None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    q = Task.select().where(Task.archived_at.is_null(True))
    if status:
        q = q.where(Task.status == status)
    if client_id:
        q = q.where(Task.client == client_id)
    rows = list(q.order_by(Task.position, Task.id).limit(limit))
    return {"count": len(rows), "tasks": [_task_row(t) for t in rows]}


def _tool_get_task(*, task_id: int) -> dict:
    try:
        t = Task.get_by_id(task_id)
    except Task.DoesNotExist:
        return {"error": f"No task #{task_id}."}
    return {**_task_row(t), "description": t.description}


def _tool_search(*, query: str, limit: int = 10) -> dict:
    """Plain substring search across Client + Task — no FTS index, just
    name/email/company and title/description LIKE matches. Small enough for
    this app's scale, and matches the per-page search boxes on the Clients
    and Tasks list pages."""
    limit = max(1, min(int(limit or 10), 30))
    query = (query or "").strip()
    hits: list[dict] = []
    if not query:
        return {"query": query, "hits": hits}
    clients = (
        Client.select()
        .where(Client.name.contains(query) | Client.email.contains(query) | Client.company.contains(query))
        .limit(limit)
    )
    for c in clients:
        hits.append({"subject_type": "client", "subject_id": c.id, "title": c.name, "snippet": c.company})
    tasks = (
        Task.select()
        .where(Task.title.contains(query) | Task.description.contains(query))
        .limit(limit)
    )
    for t in tasks:
        hits.append({"subject_type": "task", "subject_id": t.id, "title": t.title, "snippet": t.description[:120]})
    for spec in _SEARCHABLE:
        rows = spec["model"].select().where(spec["match"](query)).limit(limit)
        for r in rows:
            hits.append({
                "subject_type": spec["type"], "subject_id": r.id,
                "title": str(getattr(r, spec["title"]))[:120],
                "snippet": str(getattr(r, spec["snippet"]) or "")[:120],
            })
    return {"query": query, "hits": hits[:limit]}


# Pro's records, folded into the same substring search. A table rather than
# six more copy-pasted blocks above: adding a record type to search is a row.
_SEARCHABLE = (
    {"type": "project", "model": Project, "title": "name", "snippet": "description",
     "match": lambda q: Project.name.contains(q) | Project.description.contains(q)},
    {"type": "opportunity", "model": Opportunity, "title": "title", "snippet": "notes",
     "match": lambda q: Opportunity.title.contains(q) | Opportunity.notes.contains(q)},
    {"type": "commitment", "model": Commitment, "title": "description", "snippet": "status",
     "match": lambda q: Commitment.description.contains(q)},
    {"type": "decision", "model": Decision, "title": "title", "snippet": "rationale",
     "match": lambda q: Decision.title.contains(q) | Decision.decision.contains(q) | Decision.rationale.contains(q)},
    {"type": "person", "model": Person, "title": "name", "snippet": "role",
     "match": lambda q: Person.name.contains(q) | Person.role.contains(q)},
    {"type": "note", "model": Note, "title": "title", "snippet": "body",
     "match": lambda q: Note.title.contains(q) | Note.body.contains(q)},
)


# ---------------------------------------------------------------------------
# Pro's read tools.
#
# All read-only, so none of them appears in _MUTATING_TOOLS and none needs a
# confirmation branch. That's the deliberate shape of §8 in the spec: the
# assistant explains the business and extracts records from text, but the only
# things it can write through chat are the Client/Task tools below, which still
# pause for a human.
#
# Same contract as the tools above: keyword-only args, always return a dict,
# never raise.
# ---------------------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _tool_list_opportunities(
    *, stage: str | None = None, stalled_days: int | None = None, limit: int = 20
) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    q = Opportunity.select().where(Opportunity.archived_at.is_null(True))
    if stage:
        q = q.where(Opportunity.stage == stage)
    else:
        # Won and lost deals are history, not pipeline. Asking for them
        # explicitly by stage still works; they just don't pad the default
        # list or the pipeline total.
        q = q.where(Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES))
    if stalled_days:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=int(stalled_days))
        q = q.where(
            (Opportunity.last_activity_at < cutoff)
            & Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES)
        )
    rows = list(q.order_by(Opportunity.value.desc()).limit(limit))
    return {
        "count": len(rows),
        "pipeline_value": sum(o.value for o in rows),
        "opportunities": [{
            "id": o.id, "title": o.title, "value": o.value, "stage": o.stage,
            "customer": o.customer.name if o.customer_id else None,
            "owner": o.owner.name if o.owner_id else None,
            "next_action": o.next_action or None,
            "next_action_due": _iso(o.next_action_due),
            "days_since_activity": insights.days_quiet(o.last_activity_at),
        } for o in rows],
    }


def _tool_list_projects(*, status: str | None = None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    q = Project.select().where(Project.archived_at.is_null(True))
    if status:
        q = q.where(Project.status == status)
    rows = list(q.order_by(Project.due_date.asc(nulls="LAST")).limit(limit))
    out = []
    for p in rows:
        tasks = list(Task.select().where((Task.project == p) & Task.archived_at.is_null(True)))
        open_tasks = [t for t in tasks if t.status != "done"]
        out.append({
            "id": p.id, "name": p.name, "status": p.status,
            "owner": p.owner.name if p.owner_id else None,
            "customer": p.customer.name if p.customer_id else None,
            "due_date": _iso(p.due_date),
            "days_late": max(0, insights.days_late(p.due_date)) if p.due_date else 0,
            "open_tasks": len(open_tasks),
            "overdue_tasks": len([t for t in open_tasks if t.due_date and t.due_date < insights.today()]),
        })
    return {"count": len(out), "projects": out}


def _tool_list_commitments(
    *, status: str | None = None, person: str | None = None,
    overdue_only: bool = False, limit: int = 20,
) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    q = Commitment.select()
    if status:
        q = q.where(Commitment.status == status)
    if overdue_only:
        q = q.where(
            (Commitment.status == "open")
            & Commitment.due_date.is_null(False)
            & (Commitment.due_date < insights.today())
        )
    if person:
        matches = [p.id for p in Person.select().where(Person.name.contains(person))]
        if not matches:
            return {"count": 0, "commitments": [], "note": f"Nobody here matches “{person}”."}
        q = q.where(Commitment.person.in_(matches))
    rows = list(q.order_by(Commitment.due_date.asc(nulls="LAST")).limit(limit))
    return {
        "count": len(rows),
        "commitments": [{
            "id": c.id, "description": c.description,
            "person": c.person.name if c.person_id else None,
            "due_date": _iso(c.due_date),
            # The derived status, not the stored one — "overdue" is what a
            # person means, and the model should say the same word they would.
            "status": insights.commitment_display_status(c),
            "customer": c.customer.name if c.customer_id else None,
            "project": c.project.name if c.project_id else None,
            "source": c.source,
        } for c in rows],
    }


def _tool_list_decisions(*, query: str | None = None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    q = Decision.select()
    if query:
        q = q.where(
            Decision.title.contains(query)
            | Decision.decision.contains(query)
            | Decision.rationale.contains(query)
        )
    rows = list(q.order_by(Decision.decided_on.desc(), Decision.id.desc()).limit(limit))
    return {
        "count": len(rows),
        "decisions": [{
            "id": d.id, "title": d.title, "decision": d.decision,
            "rationale": d.rationale, "status": d.status,
            "owner": d.owner.name if d.owner_id else None,
            "decided_on": _iso(d.decided_on), "review_on": _iso(d.review_on),
            "project": d.project.name if d.project_id else None,
            "customer": d.customer.name if d.customer_id else None,
        } for d in rows],
    }


def _tool_list_people() -> dict:
    rows = list(Person.select().order_by(Person.name))
    out = []
    for p in rows:
        open_commitments = [c for c in p.commitments if c.status == "open"]
        out.append({
            "id": p.id, "name": p.name, "role": p.role or None, "active": p.active,
            "open_tasks": Task.select().where(
                (Task.owner == p) & Task.archived_at.is_null(True) & (Task.status != "done")
            ).count(),
            "open_commitments": len(open_commitments),
            "overdue_commitments": len([
                c for c in open_commitments if c.due_date and c.due_date < insights.today()
            ]),
        })
    return {"count": len(out), "people": out}


def _tool_get_attention() -> dict:
    """The dashboard and control center, as data.

    Calls the same insights.py functions the pages render, so the assistant
    physically cannot give an answer that contradicts the screen the user is
    looking at — which is the whole reason this tool exists rather than
    letting the model assemble the picture from six list calls."""
    def flatten(rows):
        return [{
            "when": f"{r['gutter']} {r['note']}".strip(),
            "what": r["primary"], "detail": r["secondary"],
        } for r in rows]

    return {
        "attention": flatten(insights.attention_rows()),
        "snapshot": {s["label"]: s["value"] for s in insights.snapshot()},
        "overdue": flatten(insights.overdue_rows()),
        "at_risk": flatten(insights.at_risk_projects()),
        "stalled": flatten(insights.stalled_rows()),
        "unassigned": flatten(insights.unassigned_rows()),
        "commitments_this_week": [{
            "person": c.person.name if c.person_id else None,
            "commitment": c.description,
            "due": _iso(c.due_date),
            "status": insights.commitment_display_status(c),
        } for c in insights.commitments_this_week()],
        "decisions_due_for_review": [d.title for d in insights.decisions_due_for_review()],
    }


# ---------------------------------------------------------------------------
# Write tools — mirror pages.py's Client/Task CRUD routes exactly (same field
# lists, same record_activity/notify calls) so an AI-driven write behaves
# identically to a human using the form UI. Never raise across the tool
# boundary — bad ids/status/missing fields all come back as {"error": ...}
# so the model (and eventually the human, via _describe_tool_call) sees a
# clean message instead of a stack trace. `actor` is injected by
# _execute_tool, not part of the tool's JSON schema the model sees.
# ---------------------------------------------------------------------------


def _tool_create_client(*, actor, name: str, email: str = "", phone: str = "",
                         company: str = "", status: str = "lead", notes: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        return {"error": "A client needs a name."}
    if status not in CLIENT_STATUSES:
        return {"error": f"Invalid status {status!r}; must be one of {CLIENT_STATUSES}."}
    client = Client.create(
        name=name, email=(email or "").strip(), phone=(phone or "").strip(),
        company=(company or "").strip(), status=status, notes=(notes or "").strip(),
        created_by=actor,
    )
    record_activity("client", client.id, actor, "created")
    return {"ok": True, **_client_row(client)}


def _tool_update_client(*, actor, client_id: int, name: str | None = None, email: str | None = None,
                         phone: str | None = None, company: str | None = None,
                         status: str | None = None, notes: str | None = None) -> dict:
    try:
        client = Client.get_by_id(client_id)
    except Client.DoesNotExist:
        return {"error": f"No client #{client_id}."}
    if client.archived_at is not None:
        return {"error": f"Client #{client_id} is archived; there is no unarchive tool."}
    if status is not None and status not in CLIENT_STATUSES:
        return {"error": f"Invalid status {status!r}; must be one of {CLIENT_STATUSES}."}
    changed = False
    for field, value in (("name", name), ("email", email), ("phone", phone),
                          ("company", company), ("status", status), ("notes", notes)):
        if value is not None:
            setattr(client, field, value.strip() if isinstance(value, str) else value)
            changed = True
    if not changed:
        return {"error": "No fields supplied to update."}
    client.updated_at = datetime.datetime.now()
    client.save()
    record_activity("client", client.id, actor, "updated")
    return {"ok": True, **_client_row(client)}


def _tool_archive_client(*, actor, client_id: int) -> dict:
    try:
        client = Client.get_by_id(client_id)
    except Client.DoesNotExist:
        return {"error": f"No client #{client_id}."}
    if client.archived_at is not None:
        return {"error": f"Client #{client_id} is already archived."}
    client.archived_at = datetime.datetime.now()
    client.save()
    record_activity("client", client.id, actor, "archived")
    return {"ok": True, "id": client.id, "name": client.name}


def _tool_create_task(*, actor, title: str, description: str = "", status: str = "todo",
                       client_id: int | None = None, assignee_id: int | None = None) -> dict:
    title = (title or "").strip()
    if not title:
        return {"error": "A task needs a title."}
    if status not in TASK_STATUSES:
        return {"error": f"Invalid status {status!r}; must be one of {TASK_STATUSES}."}
    client_obj = None
    if client_id:
        try:
            client_obj = Client.get_by_id(client_id)
        except Client.DoesNotExist:
            return {"error": f"No client #{client_id}."}
    assignee_obj = None
    if assignee_id:
        try:
            assignee_obj = User.get_by_id(assignee_id)
        except User.DoesNotExist:
            return {"error": f"No user #{assignee_id}."}
    last = Task.select().order_by(Task.position.desc()).first()
    task = Task.create(
        title=title, description=(description or "").strip(), status=status,
        client=client_obj, assignee=assignee_obj,
        position=(last.position + 1) if last else 0, created_by=actor,
    )
    record_activity("task", task.id, actor, "created")
    if task.assignee_id and task.assignee_id != actor.id:
        notify(task.assignee, "assignment", task_id=task.id, task_title=task.title)
    return {"ok": True, **_task_row(task)}


def _tool_update_task(*, actor, task_id: int, title: str | None = None, description: str | None = None,
                       status: str | None = None, client_id: int | None = None,
                       assignee_id: int | None = None) -> dict:
    try:
        task = Task.get_by_id(task_id)
    except Task.DoesNotExist:
        return {"error": f"No task #{task_id}."}
    if task.archived_at is not None:
        return {"error": f"Task #{task_id} is archived; there is no unarchive tool."}
    if status is not None and status not in TASK_STATUSES:
        return {"error": f"Invalid status {status!r}; must be one of {TASK_STATUSES}."}
    if client_id is not None:
        if client_id == 0:
            task.client = None
        else:
            try:
                task.client = Client.get_by_id(client_id)
            except Client.DoesNotExist:
                return {"error": f"No client #{client_id}."}
    old_status = task.status
    reassigned = False
    if title is not None:
        task.title = title.strip()
    if description is not None:
        task.description = description.strip()
    if status is not None:
        task.status = status
    if assignee_id is not None:
        if assignee_id == 0:
            new_assignee = None
        else:
            try:
                new_assignee = User.get_by_id(assignee_id)
            except User.DoesNotExist:
                return {"error": f"No user #{assignee_id}."}
        reassigned = new_assignee is not None and new_assignee.id != task.assignee_id
        task.assignee = new_assignee
    task.updated_at = datetime.datetime.now()
    task.save()
    if task.status != old_status:
        record_activity("task", task.id, actor, "status_changed", old=old_status, new=task.status)
    else:
        record_activity("task", task.id, actor, "updated")
    if reassigned and task.assignee_id != actor.id:
        notify(task.assignee, "assignment", task_id=task.id, task_title=task.title)
    return {"ok": True, **_task_row(task)}


def _tool_archive_task(*, actor, task_id: int) -> dict:
    try:
        task = Task.get_by_id(task_id)
    except Task.DoesNotExist:
        return {"error": f"No task #{task_id}."}
    if task.archived_at is not None:
        return {"error": f"Task #{task_id} is already archived."}
    task.archived_at = datetime.datetime.now()
    task.save()
    record_activity("task", task.id, actor, "archived")
    return {"ok": True, "id": task.id, "title": task.title}


_DISPATCH = {
    "list_clients": _tool_list_clients,
    "get_client": _tool_get_client,
    "list_tasks": _tool_list_tasks,
    "get_task": _tool_get_task,
    "search": _tool_search,
    "list_opportunities": _tool_list_opportunities,
    "list_projects": _tool_list_projects,
    "list_commitments": _tool_list_commitments,
    "list_decisions": _tool_list_decisions,
    "list_people": _tool_list_people,
    "get_attention": _tool_get_attention,
    "create_client": _tool_create_client,
    "update_client": _tool_update_client,
    "archive_client": _tool_archive_client,
    "create_task": _tool_create_task,
    "update_task": _tool_update_task,
    "archive_task": _tool_archive_task,
}


def _execute_tool(name: str, args: dict, *, actor) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        if name in _MUTATING_TOOLS:
            return fn(actor=actor, **args)
        return fn(**args)
    except TypeError as e:
        return {"error": f"Bad arguments to {name}: {e}"}
    except Exception as e:
        # Write tools touch the DB in ways the read tools never did (FK
        # constraints, etc.) — degrade any unexpected peewee/DB error to a
        # plain chat message instead of a 500. Doesn't change behavior for
        # the read tools above, which never raised anything but TypeError.
        return {"error": f"{name} failed: {e}"}


def _client_label(client_id) -> str:
    try:
        return f"client #{client_id} ({Client.get_by_id(client_id).name})"
    except Exception:
        return f"client #{client_id}"


def _task_label(task_id) -> str:
    try:
        return f'task #{task_id} ("{Task.get_by_id(task_id).title}")'
    except Exception:
        return f"task #{task_id}"


def _user_label(user_id) -> str:
    try:
        return User.get_by_id(user_id).name
    except Exception:
        return f"user #{user_id}"


def _describe_tool_call(name: str, args: dict) -> str:
    """Plain-language summary of a proposed write, for the confirmation UI —
    resolves ids to real names via a lookup, never echoes raw tool-call JSON."""
    if name == "create_client":
        return f'Create a new client named "{args.get("name", "?")}".'
    if name == "archive_client":
        return f"Archive {_client_label(args.get('client_id'))}."
    if name == "update_client":
        label = _client_label(args.get("client_id"))
        parts = [f"{k} to {v!r}" for k, v in args.items() if k != "client_id"]
        return f"Update {label}: set " + ", ".join(parts) + "." if parts else f"Update {label} (no changes given)."
    if name == "create_task":
        bits = [f'Create a new task titled "{args.get("title", "?")}"']
        if args.get("client_id"):
            bits.append(f"linked to {_client_label(args['client_id'])}")
        if args.get("assignee_id"):
            bits.append(f"assigned to {_user_label(args['assignee_id'])}")
        return ", ".join(bits) + "."
    if name == "archive_task":
        return f"Archive {_task_label(args.get('task_id'))}."
    if name == "update_task":
        label = _task_label(args.get("task_id"))
        parts = []
        for k, v in args.items():
            if k == "task_id":
                continue
            if k == "client_id":
                parts.append("client to " + (_client_label(v) if v else "none"))
            elif k == "assignee_id":
                parts.append("assignee to " + (_user_label(v) if v else "unassigned"))
            else:
                parts.append(f"{k} to {v!r}")
        return f"Update {label}: set " + ", ".join(parts) + "." if parts else f"Update {label} (no changes given)."
    return f"{name}({json.dumps(args, ensure_ascii=False)})"


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an assistant embedded in Binders Pro, the operating system a small \
company runs on. There is only one team using this app — no workspaces, no other tenants.

The records are: Customers (stored as clients: name, email, phone, company, status \
lead/active/inactive, notes), People (the directory of who can own work — not the same as user \
accounts), Projects, Tasks, Opportunities (deals, with a value and a stage), Commitments (someone \
promised to do a specific thing), Decisions (what was decided and *why*), and Notes (the raw text \
records were extracted from).

You have read-only tools: list_clients, get_client, list_tasks, get_task, list_opportunities, \
list_projects, list_commitments, list_decisions, list_people, get_attention, search. Use them \
proactively instead of guessing.

Reach for get_attention first on any broad question — "what needs my attention?", "what's \
stalled?", "what's falling through the cracks?". It returns exactly what the dashboard and the \
control center are showing the user, so your answer will match their screen. Use the narrower \
tools for specific questions: "what did João promise?" -> list_commitments(person="João"); \
"what did we decide about pricing?" -> list_decisions(query="pricing"), and quote the rationale, \
because the reason is the part worth having.

Overdue is never stored — it's computed from a due date against today, and the tools already \
return it that way. Don't recompute it yourself or contradict what a tool told you.

You also have write tools: create_client, update_client, archive_client, create_task, \
update_task, archive_task. Every write tool call is paused and shown to a human for explicit \
confirmation before it takes effect — you never need to ask "are you sure?" or "should I go \
ahead?" in your own words first; just call the tool, and the app's own UI handles confirming or \
cancelling. Don't tell the user a change has happened until you see the tool's actual result — a \
pending write hasn't happened yet, and it may be declined.

update_client/update_task are partial updates: only pass fields you actually intend to change; \
omitted fields are left exactly as they are. Look ids up first (list_clients/get_client/ \
list_tasks/get_task/search) rather than guessing them. There is no hard delete and no "unarchive" \
tool — archiving is the only removal action, and it's one-way.

Keep replies short and concrete. Reference records by name, not raw ids, unless the user is \
asking about a specific id. If a tool returns an error, relay it plainly rather than making \
something up."""


def _build_messages(thread: ChatThread, user_text: str) -> list[dict]:
    today = datetime.date.today()
    system = SYSTEM_PROMPT + f"\n\nToday's date is {today:%A, %Y-%m-%d}."
    history = list(
        ChatMessage.select()
        .where(ChatMessage.thread == thread)
        .order_by(ChatMessage.id.desc())
        .limit(HISTORY_MESSAGES)
    )[::-1]
    messages = [{"role": "system", "content": system}]
    for m in history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": user_text})
    return messages


def _http_json(url: str, *, payload: dict, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CTX) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise LLMError(f"HTTP {e.code}: {err_body[:400] or e.reason}")
    except urllib.error.URLError as e:
        raise LLMError(f"Network error reaching {url}: {e.reason}")
    except (OSError, json.JSONDecodeError) as e:
        raise LLMError(f"LLM error: {e}")


def _parse_tool_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return val if isinstance(val, dict) else {}
    return {}


def _post_openai(
    convo: list[dict], *, tools: list[dict] | None, key: str,
    json_mode: bool = False, max_tokens: int = 1200,
) -> dict:
    payload: dict = {"model": OPENAI_MODEL, "messages": convo, "max_completion_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        if _model_uses_reasoning_effort(OPENAI_MODEL):
            payload["reasoning_effort"] = "none"
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _http_json(
        OPENAI_BASE_URL + "/chat/completions", payload=payload, headers={"Authorization": f"Bearer {key}"}
    )
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"OpenAI returned no choices: {data}")
    return choices[0].get("message") or {}


def _post_ollama(
    convo: list[dict], *, tools: list[dict] | None,
    json_mode: bool = False, max_tokens: int = 1200,
) -> dict:
    payload: dict = {
        "model": OLLAMA_MODEL, "messages": convo, "stream": False,
        "keep_alive": "30m", "options": {"temperature": 0.3, "num_predict": max_tokens},
    }
    if tools:
        payload["tools"] = tools
    if json_mode:
        payload["format"] = "json"
    data = _http_json(OLLAMA_HOST + "/api/chat", payload=payload)
    return data.get("message") or {}


# ---------------------------------------------------------------------------
# Extraction — the one place this app asks a model for structured data rather
# than a tool call or a sentence.
#
# Both backends can be pinned to JSON (OpenAI's response_format, Ollama's
# "format": "json"), which removes the usual "strip the ```json fence" dance.
# What it does NOT remove is the need to distrust the result: a model that
# reliably returns *valid* JSON will still happily invent a field name. The
# caller (modules/capture) validates every key against an allow-list before
# any of it reaches the database, so the worst a hallucination can do here is
# get dropped.
# ---------------------------------------------------------------------------


def complete_json(system: str, user_text: str, *, max_tokens: int = 2000) -> dict:
    """Ask the configured backend for a JSON object and return it parsed.

    Raises LLMError for an unreachable backend, an HTTP failure, or a reply
    that isn't a JSON object — callers surface that to the user rather than
    silently producing an empty result, because "the AI is not configured" and
    "the AI found nothing in your text" need to read differently."""
    key = _openai_key()
    convo = [{"role": "system", "content": system}, {"role": "user", "content": user_text}]
    if key:
        msg = _post_openai(convo, tools=None, key=key, json_mode=True, max_tokens=max_tokens)
    else:
        msg = _post_ollama(convo, tools=None, json_mode=True, max_tokens=max_tokens)
    raw = (msg.get("content") or "").strip()
    if not raw:
        raise LLMError("The model returned an empty response.")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # json_mode should make this unreachable, but a local model served
        # through a shim may ignore the flag. One salvage attempt on the
        # outermost braces beats failing the whole capture.
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The model didn't return JSON.")
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            raise LLMError("The model didn't return JSON.")
    if not isinstance(parsed, dict):
        raise LLMError("The model returned JSON, but not an object.")
    return parsed


class _NeedsConfirmation:
    """Sentinel returned by _agent_loop when the model proposes one or more
    mutating tool calls — they must be confirmed by a human (via pages.py's
    /chat/confirm or /chat/cancel) before _execute_tool actually runs them."""

    def __init__(self, convo: list[dict], pending_calls: list[dict], round_idx: int):
        self.convo = convo
        self.pending_calls = pending_calls  # raw tool_call dicts (id/function.name/function.arguments)
        self.round_idx = round_idx


class PendingActionError(RuntimeError):
    """Raised by send_message() when a new message arrives while this
    thread already has a write awaiting confirmation."""


def _run_tool_call(tc: dict, actor) -> dict:
    fn = tc.get("function") or {}
    return _execute_tool(fn.get("name", ""), _parse_tool_args(fn.get("arguments")), actor=actor)


def _append_tool_result(convo: list[dict], tc: dict, result: dict) -> None:
    tool_msg: dict = {"role": "tool", "content": json.dumps(result, ensure_ascii=False)}
    if tc.get("id"):
        tool_msg["tool_call_id"] = tc["id"]
    else:
        tool_msg["tool_name"] = (tc.get("function") or {}).get("name", "")
    convo.append(tool_msg)


def _agent_loop(convo: list[dict], *, backend_name: str, key: str | None = None,
                 actor, start_round: int = 0) -> str | _NeedsConfirmation:
    for round_idx in range(start_round, MAX_TOOL_ROUNDTRIPS + 1):
        tools = TOOLS_SCHEMA if round_idx < MAX_TOOL_ROUNDTRIPS else None
        msg = _post_openai(convo, tools=tools, key=key or "") if backend_name == "openai" else _post_ollama(convo, tools=tools)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            text = (msg.get("content") or "").strip()
            if not text:
                raise LLMError(f"{backend_name} returned an empty response.")
            return text

        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})

        immediate_tcs, mutating_tcs = [], []
        for tc in tool_calls:
            name = (tc.get("function") or {}).get("name", "")
            (mutating_tcs if name in _MUTATING_TOOLS else immediate_tcs).append(tc)

        for tc in immediate_tcs:
            _append_tool_result(convo, tc, _run_tool_call(tc, actor))

        if mutating_tcs:
            # Reads in this same round (if any) already ran above and their
            # results are in `convo` — only the writes pause. Resuming
            # continues at round_idx + 1, so pausing costs no extra
            # roundtrip budget.
            return _NeedsConfirmation(convo, mutating_tcs, round_idx)

    raise LLMError("Tool loop exceeded — the model never produced a final answer.")


def _save_pending(thread: ChatThread, nc: _NeedsConfirmation) -> None:
    thread.pending_convo = json.dumps(nc.convo, ensure_ascii=False)
    thread.pending_tool_calls = json.dumps(nc.pending_calls, ensure_ascii=False)
    thread.pending_round_idx = nc.round_idx
    thread.save()


def _clear_pending(thread: ChatThread) -> None:
    thread.pending_convo = None
    thread.pending_tool_calls = None
    thread.pending_round_idx = None
    thread.save()


def pending_state(thread: ChatThread | None) -> list[dict] | None:
    """For chat_page: [{'name': ..., 'description': ...}, ...] if this
    thread has a write awaiting confirmation, else None."""
    if not thread or not thread.pending_tool_calls:
        return None
    out = []
    for tc in json.loads(thread.pending_tool_calls):
        fn = tc.get("function") or {}
        name = fn.get("name", "")
        args = _parse_tool_args(fn.get("arguments"))
        out.append({"name": name, "description": _describe_tool_call(name, args)})
    return out


def send_message(user, text: str) -> tuple[ChatMessage, ChatMessage | None]:
    """Persist a user turn, call the active LLM backend, persist the reply.
    Raises LLMError (with the user turn rolled back) if the backend fails —
    including when neither OPENAI_API_KEY nor a reachable Ollama is set up,
    so the caller can show a clear message instead of crashing. Raises
    PendingActionError instead if this thread already has a write awaiting
    confirmation. Returns (user_msg, None) — no assistant reply yet — if the
    model's answer is itself a new pending write."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty message")

    thread, _ = ChatThread.get_or_create(user=user)
    if thread.pending_tool_calls:
        raise PendingActionError("Resolve the pending action before sending a new message.")

    user_msg = ChatMessage.create(thread=thread, role="user", content=text)
    messages = _build_messages(thread, text)

    key = _openai_key()
    backend_name = "openai" if key else "ollama"
    try:
        result = _agent_loop(messages, backend_name=backend_name, key=key, actor=user)
    except LLMError:
        user_msg.delete_instance()
        raise

    if isinstance(result, _NeedsConfirmation):
        _save_pending(thread, result)
        return user_msg, None

    assistant_msg = ChatMessage.create(thread=thread, role="assistant", content=result)
    return user_msg, assistant_msg


def resolve_pending(thread: ChatThread, *, approved: bool) -> ChatMessage | None:
    """Execute (or decline) the pending write(s), resume the same
    conversation, and persist the model's follow-up as a new assistant
    ChatMessage. Returns None if the model's follow-up is itself another
    pending write (a chained confirmation) rather than a final answer.
    Raises ValueError if there's nothing pending on this thread."""
    if not thread.pending_tool_calls:
        raise ValueError("No pending action to resolve.")

    convo = json.loads(thread.pending_convo)
    pending_calls = json.loads(thread.pending_tool_calls)
    round_idx = thread.pending_round_idx or 0
    actor = thread.user

    for tc in pending_calls:
        result = _run_tool_call(tc, actor) if approved else {"error": "The user declined this action."}
        _append_tool_result(convo, tc, result)

    # Note: an approved write above is already committed to the DB. If the
    # follow-up LLM call below fails, that write is NOT rolled back — it's a
    # completed action being reported on, not a draft, unlike send_message's
    # rollback of a pure-read user turn.
    _clear_pending(thread)

    key = _openai_key()
    backend_name = "openai" if key else "ollama"
    try:
        result = _agent_loop(convo, backend_name=backend_name, key=key, actor=actor, start_round=round_idx + 1)
    except LLMError as e:
        verb = "completed" if approved else "cancelled"
        text = f"(The action was {verb}, but I couldn't get a follow-up reply: {e})"
        return ChatMessage.create(thread=thread, role="assistant", content=text)

    if isinstance(result, _NeedsConfirmation):
        _save_pending(thread, result)
        return None

    return ChatMessage.create(thread=thread, role="assistant", content=result)


# ---------------------------------------------------------------------------
# Hand-rolled Markdown -> HTML (render-only, ~65 lines). Headers, bold/
# italic, inline/fenced code, links restricted to http(s), ordered/
# unordered lists. HTML-escaped throughout — model output is untrusted.
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)\n```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\d+\.\s+(.*)$")


def _inline(text: str) -> str:
    text = _html.escape(text)
    text = _INLINE_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}" rel="noopener noreferrer" target="_blank">{m.group(1)}</a>', text
    )
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    return text


def render_markdown(raw: str) -> str:
    text = (raw or "").replace("\r\n", "\n")
    code_blocks: list[str] = []

    def _stash_fence(m: re.Match) -> str:
        cls = f' class="language-{m.group(1)}"' if m.group(1) else ""
        code_blocks.append(f"<pre><code{cls}>{_html.escape(m.group(2))}</code></pre>")
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    text = _FENCE_RE.sub(_stash_fence, text)

    out: list[str] = []
    open_list: str | None = None

    def _close_list() -> None:
        nonlocal open_list
        if open_list:
            out.append(f"</{open_list}>")
            open_list = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            _close_list()
            continue
        m = re.fullmatch(r"\x00CODEBLOCK(\d+)\x00", line)
        if m:
            _close_list()
            out.append(code_blocks[int(m.group(1))])
            continue
        m = _HEADER_RE.match(line)
        if m:
            _close_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        m = _UL_RE.match(line)
        if m:
            if open_list != "ul":
                _close_list()
                out.append("<ul>")
                open_list = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        m = _OL_RE.match(line)
        if m:
            if open_list != "ol":
                _close_list()
                out.append("<ol>")
                open_list = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        _close_list()
        out.append(f"<p>{_inline(line)}</p>")

    _close_list()
    return "\n".join(out)
