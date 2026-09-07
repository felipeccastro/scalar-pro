"""Text in, proposed records out.

The pipeline has four stages and the interesting one is the third:

    ai.complete_json()  ->  _clean()  ->  proposal_rows()  ->  create_records()
       model output        allow-list      review screen      writes to the DB

`_clean()` exists because the model is a source of *suggestions*, not a source
of truth. It walks the parsed JSON against RECORD_TYPES and keeps only known
record types with known fields and plausible values; anything else is dropped
silently. A hallucinated `"delete_all": true` never becomes anything, because
there is no rule that would let it through.

Nothing here writes to the database until a human has ticked the boxes and
pressed the button. `create_records()` is called from the confirm route, never
from the extract route.
"""

from __future__ import annotations

import datetime
import json

from peewee import fn

from models import (
    Client,
    CLIENT_STATUSES,
    Commitment,
    COMMITMENT_STATUSES,
    Decision,
    DECISION_STATUSES,
    Note,
    NoteLink,
    Opportunity,
    OPPORTUNITY_STAGES,
    Person,
    Project,
    PROJECT_STATUSES,
    Task,
    TASK_PRIORITIES,
    TASK_STATUSES,
    status_label,
)
from utils import parse_date, record_activity
import ai
import insights

# The contract, in one place. Each record type declares the fields it accepts,
# their kind, and (for enums) the permitted values. This drives the prompt, the
# validation, and the review screen's labels — so they cannot fall out of step
# with each other the way three hand-maintained lists would.
#
# `tag` is the mono badge in the review ledger's gutter. Four characters, so
# the column stays the same width as the day counts on the other screens.
RECORD_TYPES: dict[str, dict] = {
    "customers": {
        "tag": "CUST", "label": "Customer", "singular": "customer",
        "title": "name",
        "fields": {
            "name": {"kind": "text", "required": True},
            "status": {"kind": "enum", "values": CLIENT_STATUSES, "default": "lead"},
            "notes": {"kind": "long"},
        },
    },
    "people": {
        "tag": "WHO", "label": "Person", "singular": "person",
        "title": "name",
        "fields": {
            "name": {"kind": "text", "required": True},
            "role": {"kind": "text"},
            "email": {"kind": "text"},
        },
    },
    "opportunities": {
        "tag": "DEAL", "label": "Opportunity", "singular": "opportunity",
        "title": "title",
        "fields": {
            "title": {"kind": "text", "required": True},
            "customer": {"kind": "ref"},
            "value": {"kind": "money"},
            "stage": {"kind": "enum", "values": OPPORTUNITY_STAGES, "default": "lead"},
            "owner": {"kind": "ref"},
            "next_action": {"kind": "text"},
            "next_action_due": {"kind": "date"},
            "notes": {"kind": "long"},
        },
    },
    "projects": {
        "tag": "PROJ", "label": "Project", "singular": "project",
        "title": "name",
        "fields": {
            "name": {"kind": "text", "required": True},
            "customer": {"kind": "ref"},
            "owner": {"kind": "ref"},
            "status": {"kind": "enum", "values": PROJECT_STATUSES, "default": "planning"},
            "due_date": {"kind": "date"},
            "description": {"kind": "long"},
        },
    },
    "tasks": {
        "tag": "TASK", "label": "Task", "singular": "task",
        "title": "title",
        "fields": {
            "title": {"kind": "text", "required": True},
            "owner": {"kind": "ref"},
            "customer": {"kind": "ref"},
            "project": {"kind": "ref"},
            "status": {"kind": "enum", "values": TASK_STATUSES, "default": "todo"},
            "priority": {"kind": "enum", "values": TASK_PRIORITIES, "default": "normal"},
            "due_date": {"kind": "date"},
            "description": {"kind": "long"},
        },
    },
    "commitments": {
        "tag": "PROM", "label": "Commitment", "singular": "commitment",
        "title": "description",
        "fields": {
            "description": {"kind": "text", "required": True},
            "person": {"kind": "ref"},
            "due_date": {"kind": "date"},
            "status": {"kind": "enum", "values": COMMITMENT_STATUSES, "default": "open"},
            "customer": {"kind": "ref"},
            "project": {"kind": "ref"},
        },
    },
    "decisions": {
        "tag": "DECN", "label": "Decision", "singular": "decision",
        "title": "title",
        "fields": {
            "title": {"kind": "text", "required": True},
            "decision": {"kind": "long"},
            "rationale": {"kind": "long"},
            "owner": {"kind": "ref"},
            "decided_on": {"kind": "date"},
            "review_on": {"kind": "date"},
            "status": {"kind": "enum", "values": DECISION_STATUSES, "default": "decided"},
            "customer": {"kind": "ref"},
            "project": {"kind": "ref"},
        },
    },
}

MAX_PER_TYPE = 12  # a single note proposing 40 customers is a bug, not a windfall
MAX_TEXT = 20_000  # roughly a long meeting transcript


def _schema_for_prompt() -> str:
    lines = []
    for key, spec in RECORD_TYPES.items():
        parts = []
        for field, fspec in spec["fields"].items():
            if fspec["kind"] == "enum":
                parts.append(f"{field} (one of: {'|'.join(fspec['values'])})")
            elif fspec["kind"] == "date":
                parts.append(f"{field} (YYYY-MM-DD)")
            elif fspec["kind"] == "money":
                parts.append(f"{field} (whole number, no currency symbol)")
            elif fspec["kind"] == "ref":
                parts.append(f"{field} (a name, exactly as written in the text)")
            else:
                parts.append(field)
        lines.append(f'  "{key}": [{{ {", ".join(parts)} }}]')
    return "{\n" + ",\n".join(lines) + "\n}"


SYSTEM_PROMPT = """You read messy business writing — meeting notes, emails, \
half-finished brain dumps — and pull out the records a company would want to keep.

Return a single JSON object with this shape. Every key is optional: include only \
the types you actually found, and omit a field entirely rather than inventing a \
value for it.

{schema}

Rules:
- Extract only what the text says or clearly implies. Do not invent customers, \
amounts, dates or people to fill the shape out.
- A commitment is someone saying they will do a specific thing: "João will look \
into SSO", "I'll send the proposal Friday". A task is work that needs doing with \
no promise attached. When both readings fit, prefer a commitment — it carries \
who promised it.
- Reference people and customers by the name used in the text. Do not guess ids.
- Resolve relative dates ("Friday", "next week", "end of the month") against \
today's date, given below, and write them as YYYY-MM-DD.
- Money: "$50k" is 50000, "around 1.2m" is 1200000. Whole numbers only.
- A requirement a customer states ("they need SSO before signing") is a task \
against that customer, not a separate record type.
- A competitor mentioned in passing belongs in the customer's notes, not as a \
record of its own.
- A decision needs both what was decided and, where the text gives it, why.
- Prefer few good records over many thin ones. Nothing found is a valid answer: \
return {{}}."""


def build_system_prompt() -> str:
    today = datetime.date.today()
    return (
        SYSTEM_PROMPT.format(schema=_schema_for_prompt())
        + f"\n\nToday's date is {today:%A, %Y-%m-%d}."
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _clean_value(kind: str, spec: dict, raw) -> object | None:
    """One field, coerced and range-checked. Returns None to drop it."""
    if raw is None:
        return None
    if kind == "enum":
        text = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
        return text if text in spec["values"] else spec.get("default")
    if kind == "date":
        parsed = parse_date(str(raw))
        return parsed.isoformat() if parsed else None
    if kind == "money":
        try:
            n = int(float(str(raw).replace("$", "").replace(",", "").strip()))
        except (TypeError, ValueError):
            return None
        return max(0, n)
    text = " ".join(str(raw).split()) if kind != "long" else str(raw).strip()
    if not text:
        return None
    limit = 4000 if kind == "long" else 200
    return text[:limit]


def _clean(parsed: dict) -> dict:
    """The allow-list gate. Unknown record types, unknown fields and unusable
    values are dropped rather than corrected — this is the boundary between
    "what a model said" and "what this app will consider writing"."""
    out: dict[str, list[dict]] = {}
    for type_key, spec in RECORD_TYPES.items():
        items = parsed.get(type_key)
        if not isinstance(items, list):
            continue
        cleaned: list[dict] = []
        for item in items[:MAX_PER_TYPE]:
            if not isinstance(item, dict):
                continue
            record: dict = {}
            for field, fspec in spec["fields"].items():
                value = _clean_value(fspec["kind"], fspec, item.get(field))
                if value is not None:
                    record[field] = value
            if not record.get(spec["title"]):
                continue  # a record with no name is not a record
            cleaned.append(record)
        if cleaned:
            out[type_key] = cleaned
    return out


def extract(text: str) -> dict:
    """Run a note through the model and return the validated proposal.

    Raises ai.LLMError when no backend answers — the caller turns that into
    "Capture needs an AI backend", which is a different message from "nothing
    was found" and needs to stay that way."""
    body = (text or "").strip()[:MAX_TEXT]
    if not body:
        return {}
    return _clean(ai.complete_json(build_system_prompt(), body))


def count(proposal: dict) -> int:
    return sum(len(v) for v in proposal.values())


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def proposal_rows(note: Note) -> list[dict]:
    """The stored proposal as ledger rows for the review panel.

    `key` is what the confirm form posts back — "opportunities:0" — so the
    checkboxes address positions in the saved proposal rather than round-
    tripping the record contents through the browser."""
    try:
        proposal = json.loads(note.proposal_json or "{}")
    except (ValueError, TypeError):
        return []
    rows: list[dict] = []
    for type_key, spec in RECORD_TYPES.items():
        for index, record in enumerate(proposal.get(type_key) or []):
            rows.append({
                "key": f"{type_key}:{index}",
                "gutter": spec["tag"],
                "note": "",
                "severity": "",
                "primary": str(record.get(spec["title"], ""))[:120],
                "secondary": _summarise(type_key, record),
                "url": "",
            })
    return rows


def _summarise(type_key: str, record: dict) -> str:
    """The second line of a proposed row — the fields worth showing before
    someone commits to creating it, written the way the app writes them
    everywhere else (labelled statuses, spoken dates) rather than as the raw
    values that happen to be on their way into the database."""
    spec = RECORD_TYPES[type_key]
    bits = [spec["label"]]
    for field, fspec in spec["fields"].items():
        if field == spec["title"] or field not in record:
            continue
        value = record[field]
        kind = fspec["kind"]
        if kind == "long":
            continue
        if kind == "money":
            bits.append(f"${int(value):,}")
        elif kind == "enum":
            bits.append(status_label(str(value)))
        elif kind == "date":
            bits.append(insights.due_phrase(parse_date(str(value))))
        else:
            bits.append(str(value))
    return " · ".join(bits[:5])


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def _by_exact_name(model, field, name: str):
    """Case-insensitive exact match on a name.

    Deliberately not peewee's `**` (ILIKE): the names being matched here come
    out of a model reading arbitrary prose, and a customer called "20% Club"
    would turn into a wildcard pattern. LOWER() on both sides has no such
    surprises."""
    text = (name or "").strip()
    if not text:
        return None
    return model.select().where(fn.LOWER(field) == text.lower()).first()


def _find_or_create_customer(name: str, actor) -> Client:
    """Match an existing customer before making a new one. This is what makes
    Capture land *on* Acme rather than creating a second Acme beside it."""
    existing = _by_exact_name(Client, Client.name, name)
    if existing is not None:
        return existing
    client = Client.create(name=name, company=name, status="lead", created_by=actor)
    record_activity("client", client.id, actor, "created")
    return client


def _find_or_create_person(name: str) -> Person:
    existing = _by_exact_name(Person, Person.name, name)
    if existing is not None:
        return existing
    return Person.create(name=name)


def _find_project(name: str) -> Project | None:
    return _by_exact_name(Project, Project.name, name)


def create_records(note: Note, keys: list[str], actor) -> dict:
    """Create the ticked records, link each back to the note, and log it.

    Returns `{"created": [(subject_type, id, label)], "customer": Client|None}`
    — the customer is where the caller sends the user next, because seeing the
    note's contents arranged on a real customer record is the entire point of
    the feature.
    """
    try:
        proposal = json.loads(note.proposal_json or "{}")
    except (ValueError, TypeError):
        return {"created": [], "customer": None}

    wanted: dict[str, set[int]] = {}
    for key in keys:
        type_key, _, index = key.partition(":")
        if type_key in RECORD_TYPES and index.isdigit():
            wanted.setdefault(type_key, set()).add(int(index))

    created: list[tuple[str, int, str]] = []
    primary_customer: Client | None = None
    # Customers first, then people: everything else references them by name,
    # and resolving a reference to a row created moments ago in the same pass
    # is what stitches "Acme" in the opportunity to "Acme" the customer.
    order = ["customers", "people", "projects", "opportunities", "tasks", "commitments", "decisions"]

    for type_key in order:
        spec = RECORD_TYPES[type_key]
        items = proposal.get(type_key) or []
        for index in sorted(wanted.get(type_key, ())):
            if index >= len(items):
                continue
            record = items[index]
            obj, subject_type = _create_one(type_key, record, actor)
            if obj is None:
                continue
            NoteLink.create(note=note, subject_type=subject_type, subject_id=obj.id)
            record_activity(subject_type, obj.id, actor, "extracted", note_id=note.id)
            created.append((subject_type, obj.id, str(record.get(spec["title"], ""))))
            if subject_type == "client" and primary_customer is None:
                primary_customer = obj

    if primary_customer is None:
        # No new customer, but the records may still point at an existing one —
        # follow the first reference so the user still lands somewhere useful.
        for type_key in order:
            for index in sorted(wanted.get(type_key, ())):
                items = proposal.get(type_key) or []
                if index < len(items) and items[index].get("customer"):
                    primary_customer = _by_exact_name(
                        Client, Client.name, items[index]["customer"]
                    )
                    if primary_customer:
                        break
            if primary_customer:
                break

    note.captured = True
    note.proposal_json = ""
    note.updated_at = datetime.datetime.now()
    note.save()
    return {"created": created, "customer": primary_customer}


def _create_one(type_key: str, record: dict, actor):
    """One proposed record to one row. Returns (object, subject_type)."""
    get = record.get

    if type_key == "customers":
        client = _by_exact_name(Client, Client.name, get("name"))
        if client is not None:
            # Already known. Fold in anything new the note said about them
            # rather than creating a duplicate.
            if get("notes"):
                client.notes = (client.notes + "\n\n" + get("notes")).strip()
                client.updated_at = datetime.datetime.now()
                client.save()
            return client, "client"
        return Client.create(
            name=get("name"), company=get("name"), status=get("status") or "lead",
            notes=get("notes") or "", created_by=actor,
        ), "client"

    if type_key == "people":
        person = _by_exact_name(Person, Person.name, get("name"))
        if person is not None:
            return person, "person"
        return Person.create(
            name=get("name"), role=get("role") or "", email=get("email") or "",
        ), "person"

    if type_key == "projects":
        return Project.create(
            name=get("name"), description=get("description") or "",
            status=get("status") or "planning",
            owner=_find_or_create_person(get("owner")) if get("owner") else None,
            customer=_find_or_create_customer(get("customer"), actor) if get("customer") else None,
            due_date=parse_date(get("due_date")), created_by=actor,
        ), "project"

    if type_key == "opportunities":
        return Opportunity.create(
            title=get("title"),
            customer=_find_or_create_customer(get("customer"), actor) if get("customer") else None,
            value=int(get("value") or 0), stage=get("stage") or "lead",
            owner=_find_or_create_person(get("owner")) if get("owner") else None,
            next_action=get("next_action") or "",
            next_action_due=parse_date(get("next_action_due")),
            notes=get("notes") or "", created_by=actor,
        ), "opportunity"

    if type_key == "tasks":
        person = _find_or_create_person(get("owner")) if get("owner") else None
        last = Task.select().order_by(Task.position.desc()).first()
        return Task.create(
            title=get("title"), description=get("description") or "",
            status=get("status") or "todo", priority=get("priority") or "normal",
            owner=person,
            assignee=person.user if person and person.user_id else None,
            client=_find_or_create_customer(get("customer"), actor) if get("customer") else None,
            project=_find_project(get("project")) if get("project") else None,
            due_date=parse_date(get("due_date")),
            position=(last.position + 1) if last else 0, created_by=actor,
        ), "task"

    if type_key == "commitments":
        return Commitment.create(
            description=get("description"),
            person=_find_or_create_person(get("person")) if get("person") else None,
            due_date=parse_date(get("due_date")), status=get("status") or "open",
            source="capture",
            customer=_find_or_create_customer(get("customer"), actor) if get("customer") else None,
            project=_find_project(get("project")) if get("project") else None,
            created_by=actor,
        ), "commitment"

    if type_key == "decisions":
        return Decision.create(
            title=get("title"), decision=get("decision") or "",
            rationale=get("rationale") or "",
            owner=_find_or_create_person(get("owner")) if get("owner") else None,
            decided_on=parse_date(get("decided_on")) or datetime.date.today(),
            review_on=parse_date(get("review_on")),
            status=get("status") or "decided",
            customer=_find_or_create_customer(get("customer"), actor) if get("customer") else None,
            project=_find_project(get("project")) if get("project") else None,
            created_by=actor,
        ), "decision"

    return None, ""
