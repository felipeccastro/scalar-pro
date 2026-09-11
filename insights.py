"""Everything the dashboard and the control center know.

Both pages answer questions about *elapsed time* — what's late, what hasn't
moved, what's about to land. None of that is stored: there is no `is_overdue`
column and no nightly job to keep one honest. Every answer here is computed
from `due_date`/`last_activity_at` against today, so a database seeded three
months ago still reads correctly the moment you open it.

The two pages share this module rather than each running their own queries,
which is the only reason they can't disagree — the dashboard's "3 overdue
commitments" and the control center's overdue list are the same function call.

Output shape: every list function returns **ledger rows** (see `_row`), the
dicts `dashboard/_ledger.html` renders. Keeping the shape uniform is what lets
one template serve the dashboard's attention list, all five control-center
sections, and the Capture review.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from models import (
    Activity,
    Client,
    Commitment,
    Decision,
    Note,
    Opportunity,
    OPPORTUNITY_OPEN_STAGES,
    Person,
    Project,
    Task,
    status_label,
)
from utils import url_for

# How long something sits before it counts as neglected. Deals go quiet faster
# than projects do — a week of silence on a deal in negotiation is a problem,
# a week of silence on a six-month project is a Tuesday.
STALLED_DEAL_DAYS = 7
STALLED_PROJECT_DAYS = 14
# A deal this quiet is worth waking the CEO for, not just the COO.
CEO_STALLED_DAYS = 10
# "Deadline approaching" for the at-risk check.
AT_RISK_HORIZON_DAYS = 7
# How far ahead to surface a decision that's due to be revisited.
DECISION_REVIEW_HORIZON_DAYS = 14
ATTENTION_LIMIT = 6


# ---------------------------------------------------------------------------
# Subject registry — the one place that maps a subject_type to its model,
# detail route and display label. Comment/Attachment/Activity/NoteLink are all
# generic over (subject_type, subject_id), so anything that has to turn one
# back into a link or a name goes through here: pages/core.py's post-comment
# redirect, the activity feed, and the note's "what came out of this" list.
# ---------------------------------------------------------------------------

#  "badge" is the two-letter mark quick search's palette shows next to a hit
#  (see search_palette() in pages/core.py) — two letters because a single
#  initial collides twice over (Customer/Commitment, Person/Project).
#  "list_query" is an extra query string subject_url() adds when landing on a
#  list rather than a detail page, for the one case (commitments) where the
#  list's default filter would otherwise hide the very row being linked to.
SUBJECT_REGISTRY: dict[str, dict[str, Any]] = {
    "client": {"model": Client, "route": "client_detail", "kwarg": "client_id", "label": "Customer", "badge": "Cu"},
    "task": {"model": Task, "route": "task_detail", "kwarg": "task_id", "label": "Task", "badge": "Tk"},
    "person": {"model": Person, "route": "people_list", "kwarg": None, "label": "Person", "badge": "Pe"},
    "project": {"model": Project, "route": "project_detail", "kwarg": "project_id", "label": "Project", "badge": "Pr"},
    "opportunity": {"model": Opportunity, "route": "opportunity_detail", "kwarg": "opportunity_id", "label": "Opportunity", "badge": "Op"},
    "commitment": {
        "model": Commitment, "route": "commitments_list", "kwarg": None, "label": "Commitment", "badge": "Cm",
        "list_query": {"filter": "all"},
    },
    "decision": {"model": Decision, "route": "decisions_list", "kwarg": None, "label": "Decision", "badge": "De"},
    "note": {"model": Note, "route": "note_detail", "kwarg": "note_id", "label": "Note", "badge": "No"},
}

# Which field on each model reads as its name in a feed or a link.
_TITLE_FIELD = {
    "client": "name", "task": "title", "person": "name", "project": "name",
    "opportunity": "title", "commitment": "description", "decision": "title",
    "note": "title",
}


def subject_url(subject_type: str, subject_id: int) -> str:
    """Best link for a (type, id) pair. Types without a detail page of their
    own (person, commitment, decision) land on their list instead of 404ing —
    those are §4/§6 work — but with a `#record-<id>` fragment appended, so the
    list still scrolls to and highlights that one row (see the `:target` rule
    in style.css) rather than just dropping you on the list in general.
    `list_query` forces whatever filter would otherwise hide that row (e.g. a
    done commitment under the list's default "open" filter)."""
    spec = SUBJECT_REGISTRY.get(subject_type)
    if spec is None:
        return url_for("dashboard")
    if spec["kwarg"] is None:
        base = url_for(spec["route"], _query=spec["list_query"]) if "list_query" in spec else url_for(spec["route"])
        return f"{base}#record-{subject_id}"
    return url_for(spec["route"], **{spec["kwarg"]: subject_id})


def subject_name(subject_type: str, subject_id: int) -> str:
    """Display name for a (type, id) pair, or a stable placeholder if the row
    is gone. Never raises — this runs inside feed rendering, where a dangling
    id must not take the page down."""
    spec = SUBJECT_REGISTRY.get(subject_type)
    if spec is None:
        return f"{subject_type} #{subject_id}"
    try:
        obj = spec["model"].get_or_none(spec["model"].id == subject_id)
    except Exception:
        obj = None
    if obj is None:
        return f"{spec['label'].lower()} #{subject_id}"
    text = str(getattr(obj, _TITLE_FIELD[subject_type], "") or "").strip()
    if not text:
        return f"{spec['label'].lower()} #{subject_id}"
    return text if len(text) <= 70 else text[:69] + "…"


def subject_label(subject_type: str) -> str:
    spec = SUBJECT_REGISTRY.get(subject_type)
    return spec["label"] if spec else subject_type.replace("_", " ").title()


def subject_badge(subject_type: str) -> str:
    spec = SUBJECT_REGISTRY.get(subject_type)
    return spec["badge"] if spec else subject_type[:2].title()


# ---------------------------------------------------------------------------
# Time and formatting
# ---------------------------------------------------------------------------


def today() -> datetime.date:
    return datetime.date.today()


def days_late(due: datetime.date | None) -> int:
    """Positive = that many days past due. 0 = due today. Negative = upcoming."""
    return (today() - due).days if due else 0


def days_quiet(since: datetime.datetime | None) -> int:
    if since is None:
        return 0
    if isinstance(since, datetime.datetime):
        since = since.date()
    return (today() - since).days


def money(value: int | None) -> str:
    return f"${int(value or 0):,}"


def money_short(value: int | None) -> str:
    """Compact form for the snapshot strip, where five metrics share one line
    and "$412,000" would crowd out the other four."""
    n = int(value or 0)
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"${n // 1000}k"
    return f"${n}"


def due_phrase(due: datetime.date | None) -> str:
    """How a person would say the date out loud. Inside a week it's a weekday
    ("due Friday"), because that's how deadlines are actually discussed;
    further out it's a date."""
    if due is None:
        return "no due date"
    delta = (due - today()).days
    if delta == 0:
        return "due today"
    if delta == 1:
        return "due tomorrow"
    if delta == -1:
        return "due yesterday"
    if 0 < delta <= 6:
        return f"due {due.strftime('%A')}"
    if -6 <= delta < 0:
        return f"was due {due.strftime('%A')}"
    return f"due {due.strftime('%b %-d')}"


def _row(
    *, gutter: str, note: str = "", severity: str = "", primary: str,
    secondary: str = "", url: str = "", sort: int = 0,
) -> dict:
    """One ledger row.

    `gutter`/`note` are the mono left column — the elapsed-time figure and its
    one-word qualifier. `severity` tints it: overdue | stalled | soon | "".
    `sort` is days-late-or-quiet, so merged lists can be ranked by how bad
    they are without re-deriving it.
    """
    return {
        "gutter": gutter, "note": note, "severity": severity,
        "primary": primary, "secondary": secondary, "url": url, "sort": sort,
    }


def _late_gutter(n: int) -> tuple[str, str, str, int]:
    """Gutter text/qualifier/severity/sort for something `n` days past due."""
    if n > 0:
        return f"{n}d", "over", "overdue", n
    if n == 0:
        return "today", "", "soon", 0
    return f"{-n}d", "left", "soon", n


# ---------------------------------------------------------------------------
# Overdue — one merged list across every kind of deadline the app tracks.
# ---------------------------------------------------------------------------


def overdue_commitments() -> list[Commitment]:
    return list(
        Commitment.select()
        .where(
            (Commitment.status == "open")
            & Commitment.due_date.is_null(False)
            & (Commitment.due_date < today())
        )
        .order_by(Commitment.due_date)
    )


def overdue_tasks() -> list[Task]:
    return list(
        Task.select()
        .where(
            Task.archived_at.is_null(True)
            & (Task.status != "done")
            & Task.due_date.is_null(False)
            & (Task.due_date < today())
        )
        .order_by(Task.due_date)
    )


def overdue_projects() -> list[Project]:
    return list(
        Project.select()
        .where(
            Project.archived_at.is_null(True)
            & (Project.status != "done")
            & Project.due_date.is_null(False)
            & (Project.due_date < today())
        )
        .order_by(Project.due_date)
    )


def overdue_opportunity_actions() -> list[Opportunity]:
    return list(
        Opportunity.select()
        .where(
            Opportunity.archived_at.is_null(True)
            & Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES)
            & Opportunity.next_action_due.is_null(False)
            & (Opportunity.next_action_due < today())
        )
        .order_by(Opportunity.next_action_due)
    )


def overdue_rows() -> list[dict]:
    """Everything late, oldest first — the control center's opening section.

    Merging the four kinds into one list is the point: a COO doesn't care
    whether the thing that slipped was modelled as a task or a commitment,
    only that it's eleven days late."""
    rows: list[dict] = []
    for c in overdue_commitments():
        g, n, sev, sort = _late_gutter(days_late(c.due_date))
        who = c.person.name if c.person_id else "nobody"
        rows.append(_row(
            gutter=g, note=n, severity=sev, sort=sort,
            primary=_clip(c.description),
            secondary=f"Commitment · {who} · {due_phrase(c.due_date)}",
            url=url_for("commitments_list"),
        ))
    for t in overdue_tasks():
        g, n, sev, sort = _late_gutter(days_late(t.due_date))
        who = t.owner.name if t.owner_id else "unassigned"
        rows.append(_row(
            gutter=g, note=n, severity=sev, sort=sort,
            primary=t.title,
            secondary=f"Task · {who} · {due_phrase(t.due_date)}",
            url=url_for("task_detail", task_id=t.id),
        ))
    for p in overdue_projects():
        g, n, sev, sort = _late_gutter(days_late(p.due_date))
        rows.append(_row(
            gutter=g, note=n, severity=sev, sort=sort,
            primary=p.name,
            secondary=f"Project · {status_label(p.status)} · {due_phrase(p.due_date)}",
            url=url_for("project_detail", project_id=p.id),
        ))
    for o in overdue_opportunity_actions():
        g, n, sev, sort = _late_gutter(days_late(o.next_action_due))
        action = o.next_action or "Next step"
        rows.append(_row(
            gutter=g, note=n, severity=sev, sort=sort,
            primary=f"{action} — {o.customer.name if o.customer_id else o.title}",
            secondary=f"Opportunity · {money(o.value)} · {status_label(o.stage)}",
            url=url_for("opportunity_detail", opportunity_id=o.id),
        ))
    rows.sort(key=lambda r: -r["sort"])
    return rows


# ---------------------------------------------------------------------------
# At risk, stalled, unassigned
# ---------------------------------------------------------------------------


def at_risk_projects() -> list[dict]:
    """Projects the COO should look at before Friday.

    Four independent triggers — flagged status, blocked, deadline inside a
    week, or carrying overdue tasks — because a project rarely announces it's
    in trouble through only one of them."""
    horizon = today() + datetime.timedelta(days=AT_RISK_HORIZON_DAYS)
    rows: list[dict] = []
    projects = list(
        Project.select()
        .where(Project.archived_at.is_null(True) & (Project.status != "done"))
        .order_by(Project.due_date)
    )
    for p in projects:
        open_tasks = list(
            Task.select().where(
                (Task.project == p) & Task.archived_at.is_null(True) & (Task.status != "done")
            )
        )
        late_tasks = [t for t in open_tasks if t.due_date and t.due_date < today()]
        deadline_close = p.due_date is not None and p.due_date <= horizon
        flagged = p.status in ("at_risk", "blocked")
        if not (flagged or late_tasks or (deadline_close and open_tasks)):
            continue
        if p.due_date and p.due_date < today():
            g, n, sev, sort = _late_gutter(days_late(p.due_date))
        elif late_tasks:
            # The project isn't late; its work is. Say which, or the gutter
            # reads as a contradiction next to a due date in the future.
            worst = max(days_late(t.due_date) for t in late_tasks)
            g, n, sev, sort = f"{worst}d", "late task", "overdue", worst
        else:
            days = (p.due_date - today()).days if p.due_date else 0
            g, n, sev, sort = (f"{days}d", "left", "soon", -days)
        bits = [due_phrase(p.due_date), f"{len(open_tasks)} tasks remaining"]
        if late_tasks:
            bits.append(f"{len(late_tasks)} overdue")
        if flagged:
            bits.append(status_label(p.status))
        rows.append(_row(
            gutter=g, note=n, severity=sev, sort=sort,
            primary=p.name, secondary=" · ".join(bits),
            url=url_for("project_detail", project_id=p.id),
        ))
    rows.sort(key=lambda r: -r["sort"])
    return rows


def stalled_opportunities(min_days: int = STALLED_DEAL_DAYS) -> list[Opportunity]:
    cutoff = datetime.datetime.now() - datetime.timedelta(days=min_days)
    return list(
        Opportunity.select()
        .where(
            Opportunity.archived_at.is_null(True)
            & Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES)
            & (Opportunity.last_activity_at < cutoff)
        )
        .order_by(Opportunity.last_activity_at)
    )


def stalled_rows() -> list[dict]:
    """Nothing is wrong with these — that's the problem. No deadline has
    passed, so they show up nowhere else; they've just gone quiet."""
    rows: list[dict] = []
    for o in stalled_opportunities():
        n = days_quiet(o.last_activity_at)
        rows.append(_row(
            gutter=f"{n}d", note="quiet", severity="stalled", sort=n,
            primary=f"{o.customer.name if o.customer_id else o.title} — no movement",
            secondary=f"Opportunity · {money(o.value)} · {status_label(o.stage)}",
            url=url_for("opportunity_detail", opportunity_id=o.id),
        ))
    cutoff = datetime.datetime.now() - datetime.timedelta(days=STALLED_PROJECT_DAYS)
    quiet_projects = (
        Project.select()
        .where(
            Project.archived_at.is_null(True)
            & (Project.status != "done")
            & (Project.last_activity_at < cutoff)
        )
        .order_by(Project.last_activity_at)
    )
    for p in quiet_projects:
        n = days_quiet(p.last_activity_at)
        rows.append(_row(
            gutter=f"{n}d", note="quiet", severity="stalled", sort=n,
            primary=f"{p.name} — no update",
            secondary=f"Project · {status_label(p.status)}",
            url=url_for("project_detail", project_id=p.id),
        ))
    rows.sort(key=lambda r: -r["sort"])
    return rows


def unassigned_rows() -> list[dict]:
    """Work nobody owns. Counts rather than individual rows: the useful
    question is "how much of this is there", and the link goes to the list
    where it can be fixed in bulk."""
    counts = [
        (
            Task.select().where(
                Task.archived_at.is_null(True) & (Task.status != "done") & Task.owner.is_null(True)
            ).count(),
            "task", "tasks", url_for("tasks_list"),
        ),
        (
            Opportunity.select().where(
                Opportunity.archived_at.is_null(True)
                & Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES)
                & Opportunity.owner.is_null(True)
            ).count(),
            "opportunity", "opportunities", url_for("opportunities_list"),
        ),
        (
            Project.select().where(
                Project.archived_at.is_null(True) & (Project.status != "done") & Project.owner.is_null(True)
            ).count(),
            "project", "projects", url_for("projects_list"),
        ),
        (
            Commitment.select().where(
                (Commitment.status == "open") & Commitment.person.is_null(True)
            ).count(),
            "commitment", "commitments", url_for("commitments_list"),
        ),
    ]
    rows = []
    for n, singular, plural, href in counts:
        if not n:
            continue
        rows.append(_row(
            gutter=str(n), note="no owner", severity="stalled", sort=n,
            primary=f"{n} {singular if n == 1 else plural} {'has' if n == 1 else 'have'} no owner",
            secondary="Nobody is going to pick this up on their own",
            url=href,
        ))
    return rows


def commitments_this_week() -> list[Commitment]:
    """Who promised what, and are they doing it. Open commitments due any time
    up to the end of this week, including ones already late — a promise that
    was due Monday is still this week's problem."""
    end = today() + datetime.timedelta(days=(6 - today().weekday()))
    return list(
        Commitment.select()
        .where(
            (Commitment.status == "open")
            & Commitment.due_date.is_null(False)
            & (Commitment.due_date <= end)
        )
        .order_by(Commitment.due_date)
    )


def commitment_display_status(c: Commitment) -> str:
    """The status a person sees, which includes the one we refuse to store."""
    if c.status == "open" and c.due_date and c.due_date < today():
        return "overdue"
    return c.status


# ---------------------------------------------------------------------------
# The CEO's four sections
# ---------------------------------------------------------------------------


def attention_rows(limit: int = ATTENTION_LIMIT) -> list[dict]:
    """The dashboard's opening ledger — what the CEO would be told first.

    Not simply the top of `overdue_rows()`. Overdue commitments collapse into
    a single line (the count is the story, not each one), and a deal that's
    gone quiet for a fortnight belongs here even though nothing about it is
    technically late."""
    rows: list[dict] = []

    late = overdue_commitments()
    if late:
        worst = max(days_late(c.due_date) for c in late)
        rows.append(_row(
            gutter=str(len(late)), note="overdue", severity="overdue", sort=1000 + worst,
            primary=(
                "1 commitment is overdue" if len(late) == 1
                else f"{len(late)} commitments are overdue"
            ),
            secondary=(
                f"Oldest is {worst} days past due · "
                + ", ".join(c.person.name for c in late[:3] if c.person_id)
            ).rstrip(" ·,"),
            url=url_for("commitments_list", _query={"filter": "overdue"}),
        ))

    for o in overdue_opportunity_actions():
        n = days_late(o.next_action_due)
        g, note, sev, _ = _late_gutter(n)
        who = o.customer.name if o.customer_id else o.title
        rows.append(_row(
            gutter=g, note=note, severity=sev, sort=500 + n,
            primary=f"{o.next_action or 'Next step'} — {who}",
            secondary=f"{money(o.value)} · {status_label(o.stage)}",
            url=url_for("opportunity_detail", opportunity_id=o.id),
        ))

    for p in overdue_projects():
        n = days_late(p.due_date)
        rows.append(_row(
            gutter=f"{n}d", note="behind", severity="overdue", sort=300 + n,
            primary=f"{p.name} is {n} days behind",
            secondary=f"Project · {status_label(p.status)}",
            url=url_for("project_detail", project_id=p.id),
        ))

    for o in stalled_opportunities(CEO_STALLED_DAYS):
        n = days_quiet(o.last_activity_at)
        who = o.customer.name if o.customer_id else o.title
        rows.append(_row(
            gutter=f"{n}d", note="quiet", severity="stalled", sort=100 + n,
            primary=f"{who} has been stalled for {n} days",
            secondary=f"{money(o.value)} · {status_label(o.stage)}",
            url=url_for("opportunity_detail", opportunity_id=o.id),
        ))

    rows.sort(key=lambda r: -r["sort"])
    # A deal can be both late on its next action and quiet for a fortnight.
    # Both are true; saying both in a six-line briefing wastes a line. Keep
    # the more severe, which sorting has already put first.
    seen: set[str] = set()
    unique = []
    for row in rows:
        if row["url"] in seen:
            continue
        seen.add(row["url"])
        unique.append(row)
    return unique[:limit]


def snapshot() -> list[dict]:
    """Five numbers, deliberately. The spec's words: don't turn this into a BI
    dashboard."""
    week_end = today() + datetime.timedelta(days=7)
    open_deals = list(
        Opportunity.select().where(
            Opportunity.archived_at.is_null(True)
            & Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES)
        )
    )
    return [
        {"label": "Pipeline", "value": money_short(sum(o.value for o in open_deals)),
         "url": url_for("opportunities_list")},
        {"label": "Open deals", "value": str(len(open_deals)),
         "url": url_for("opportunities_list")},
        {"label": "Active projects", "value": str(
            Project.select().where(
                Project.archived_at.is_null(True) & (Project.status != "done")
            ).count()),
         "url": url_for("projects_list")},
        {"label": "Overdue", "value": str(len(overdue_commitments())),
         "url": url_for("commitments_list", _query={"filter": "overdue"})},
        # Strictly ahead of today: anything already late is the tile to its
        # left, and counting it twice makes both numbers less useful.
        {"label": "Due this week", "value": str(
            Task.select().where(
                Task.archived_at.is_null(True) & (Task.status != "done")
                & Task.due_date.is_null(False)
                & (Task.due_date >= today()) & (Task.due_date <= week_end)
            ).count()),
         "url": url_for("tasks_list")},
    ]


# ---------------------------------------------------------------------------
# What's changed — the Activity table, finally rendered.
#
# Core writes `verb` + a JSON payload on every mutation but never displays the
# payload. Pro reads it back: that append-only log is already an accurate
# record of what the company did this week, so "what's changed" needs no new
# table, only a translator from (verb, payload) to a sentence.
# ---------------------------------------------------------------------------

_VERB_PHRASE = {
    "created": "created",
    "updated": "updated",
    "archived": "archived",
    "commented": "commented on",
    "attached": "attached a file to",
    "extracted": "pulled",
}


def describe_activity(a: Activity) -> dict:
    """One activity row as a sentence plus a link. Never raises: a dangling
    subject id degrades to a placeholder name rather than breaking the feed."""
    payload = _payload(a)
    name = subject_name(a.subject_type, a.subject_id)
    kind = subject_label(a.subject_type).lower()
    actor = a.actor.name if a.actor_id else "Someone"

    if a.verb == "status_changed":
        old, new = payload.get("old"), payload.get("new")
        if old and new:
            text = f"{name} moved from {status_label(old)} to {status_label(new)}"
        else:
            text = f"{name} changed status"
    elif a.verb == "captured":
        text = f"{actor} captured “{name}”"
    elif a.verb == "extracted":
        # Normally collapsed by whats_changed(); this is the single-record case
        # and the fallback for anywhere else that renders one activity row.
        text = f"{name} came out of a captured note"
    else:
        phrase = _VERB_PHRASE.get(a.verb, a.verb.replace("_", " "))
        text = f"{actor} {phrase} {kind} {name}"

    return {
        "text": text,
        "url": subject_url(a.subject_type, a.subject_id),
        "at": a.created_at,
    }


def whats_changed(days: int = 7, limit: int = 60) -> list[dict]:
    """Recent activity grouped by day, newest first — `[{"day", "items"}]`.

    One capture writes an `extracted` row per record it created, which as
    six near-identical lines would swamp a week's worth of everything else.
    They're collapsed back into the single event they actually were."""
    since = datetime.datetime.now() - datetime.timedelta(days=days)
    rows = list(
        Activity.select()
        .where(Activity.created_at >= since)
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    groups: list[dict] = []
    for a in rows:
        day = a.created_at.date() if isinstance(a.created_at, datetime.datetime) else a.created_at
        if not groups or groups[-1]["day"] != day:
            groups.append({"day": day, "items": [], "_extracted": {}})
        group = groups[-1]

        if a.verb == "extracted":
            note_id = _payload(a).get("note_id")
            existing = group["_extracted"].get(note_id)
            if existing is not None:
                existing["count"] += 1
                existing["text"] = _extracted_phrase(existing["count"], note_id)
                continue
            item = {
                "text": _extracted_phrase(1, note_id),
                "url": subject_url("note", note_id) if note_id else url_for("notes_list"),
                "at": a.created_at,
                "count": 1,
            }
            group["_extracted"][note_id] = item
            group["items"].append(item)
            continue

        group["items"].append(describe_activity(a))

    for group in groups:
        group.pop("_extracted", None)
    return groups


def _extracted_phrase(count: int, note_id) -> str:
    title = subject_name("note", note_id) if note_id else "a note"
    return f"{count} record{'' if count == 1 else 's'} came out of “{title}”"


def _payload(a: Activity) -> dict:
    try:
        value = json.loads(a.payload_json or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def day_label(day: datetime.date) -> str:
    delta = (today() - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return day.strftime("%A %b %-d") if delta < 7 else day.strftime("%b %-d")


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def recent_decisions(limit: int = 5) -> list[Decision]:
    return list(Decision.select().order_by(Decision.decided_on.desc(), Decision.id.desc()).limit(limit))


def decisions_due_for_review() -> list[Decision]:
    horizon = today() + datetime.timedelta(days=DECISION_REVIEW_HORIZON_DAYS)
    return list(
        Decision.select()
        .where(
            (Decision.status != "superseded")
            & Decision.review_on.is_null(False)
            & (Decision.review_on <= horizon)
        )
        .order_by(Decision.review_on)
    )


# ---------------------------------------------------------------------------


def _clip(text: str, length: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= length else text[: length - 1] + "…"
