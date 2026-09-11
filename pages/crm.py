"""Sales: the pipeline and the people in it.

Customers themselves stay in pages/core.py — they're Core's `Client` and
every part of the app touches them. What lives here is what turns a customer
list into a pipeline: opportunities, and the directory of people who own
them.
"""

from __future__ import annotations

import datetime

from bottle import request

from app import app, render
from models import (
    Opportunity,
    OPPORTUNITY_STAGES,
    Person,
    Task,
    User,
)
from pages.core import _active_clients, _linked_notes, _load_activity, _load_attachments, _load_comments, _people
from utils import (
    current_user,
    flash,
    parse_date,
    parse_int,
    record_activity,
    redirect,
    require_login,
    url_for,
)
import insights
import search


def _touch(opportunity: Opportunity) -> None:
    """Every write marks the deal as having moved. `last_activity_at` is the
    only thing standing between "stalled" being a real signal and being
    decoration — if a write path forgets to call this, the deal will look
    abandoned the moment someone stops editing it from this page."""
    now = datetime.datetime.now()
    opportunity.last_activity_at = now
    opportunity.updated_at = now


@app.route("/opportunities", method="GET", name="opportunities_list")
@require_login
def opportunities_list():
    q = (request.query.get("q") or "").strip()
    query = Opportunity.select().where(Opportunity.archived_at.is_null(True))
    if q:
        query = query.where(Opportunity.title.contains(q) | Opportunity.notes.contains(q))
    deals = list(query.order_by(Opportunity.value.desc()))
    # Grouped by stage rather than listed flat: a pipeline's shape is the
    # information, and a table sorted by value hides it.
    grouped = {s: [o for o in deals if o.stage == s] for s in OPPORTUNITY_STAGES}
    return render(
        "crm/opportunities_list.html",
        grouped=grouped,
        stages=OPPORTUNITY_STAGES,
        clients=_active_clients(),
        people=_people(),
        insights=insights,
        q=q,
    )


@app.route("/opportunities", method="POST", name="opportunities_create")
@require_login
def opportunities_create():
    title = (request.forms.get("title") or "").strip()
    if not title:
        flash("An opportunity needs a name.", "error")
        redirect(url_for("opportunities_list"))
    opportunity = Opportunity.create(
        title=title,
        customer=parse_int(request.forms.get("customer_id")),
        value=parse_int(request.forms.get("value")) or 0,
        stage=request.forms.get("stage") or "lead",
        owner=parse_int(request.forms.get("owner_id")),
        next_action=(request.forms.get("next_action") or "").strip(),
        next_action_due=parse_date(request.forms.get("next_action_due")),
        notes=(request.forms.get("notes") or "").strip(),
        created_by=current_user(),
    )
    record_activity("opportunity", opportunity.id, current_user(), "created")
    search.index_entity(opportunity)
    flash(f"Added {opportunity.title}.", "success")
    redirect(url_for("opportunity_detail", opportunity_id=opportunity.id))


@app.route("/opportunities/<opportunity_id:int>", method="GET", name="opportunity_detail")
@require_login
def opportunity_detail(opportunity_id: int):
    opportunity = Opportunity.get_or_none(Opportunity.id == opportunity_id)
    if opportunity is None:
        flash("That opportunity doesn't exist.", "error")
        redirect(url_for("opportunities_list"))
    return render(
        "crm/opportunity_detail.html",
        opportunity=opportunity,
        stages=OPPORTUNITY_STAGES,
        clients=_active_clients(),
        people=_people(),
        notes=_linked_notes("opportunity", opportunity.id),
        comments=_load_comments("opportunity", opportunity.id),
        attachments=_load_attachments("opportunity", opportunity.id),
        activity=_load_activity("opportunity", opportunity.id),
        insights=insights,
    )


@app.route("/opportunities/<opportunity_id:int>", method="POST", name="opportunity_update")
@require_login
def opportunity_update(opportunity_id: int):
    opportunity = Opportunity.get_or_none(Opportunity.id == opportunity_id)
    if opportunity is None:
        flash("That opportunity doesn't exist.", "error")
        redirect(url_for("opportunities_list"))
    old_stage = opportunity.stage
    opportunity.title = (request.forms.get("title") or opportunity.title).strip()
    opportunity.customer = parse_int(request.forms.get("customer_id"))
    opportunity.value = parse_int(request.forms.get("value")) or 0
    opportunity.stage = request.forms.get("stage") or opportunity.stage
    opportunity.owner = parse_int(request.forms.get("owner_id"))
    opportunity.next_action = (request.forms.get("next_action") or "").strip()
    opportunity.next_action_due = parse_date(request.forms.get("next_action_due"))
    opportunity.notes = (request.forms.get("notes") or "").strip()
    _touch(opportunity)
    opportunity.save()
    if opportunity.stage != old_stage:
        record_activity(
            "opportunity", opportunity.id, current_user(), "status_changed",
            old=old_stage, new=opportunity.stage,
        )
    else:
        record_activity("opportunity", opportunity.id, current_user(), "updated")
    search.index_entity(opportunity)
    flash("Opportunity updated.", "success")
    redirect(url_for("opportunity_detail", opportunity_id=opportunity.id))


@app.route("/opportunities/<opportunity_id:int>/archive", method="POST", name="opportunity_archive")
@require_login
def opportunity_archive(opportunity_id: int):
    opportunity = Opportunity.get_or_none(Opportunity.id == opportunity_id)
    if opportunity is not None:
        opportunity.archived_at = datetime.datetime.now()
        opportunity.save()
        record_activity("opportunity", opportunity.id, current_user(), "archived")
        flash(f"Archived {opportunity.title}.", "success")
    redirect(url_for("opportunities_list"))


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@app.route("/people", method="GET", name="people_list")
@require_login
def people_list():
    people = list(Person.select().order_by(Person.active.desc(), Person.name))
    # What each person is actually carrying. The directory is only useful
    # alongside the load — a name with eleven open tasks next to it is a
    # different conversation from a name with none.
    load = {
        p.id: {
            "tasks": Task.select().where(
                (Task.owner == p) & Task.archived_at.is_null(True) & (Task.status != "done")
            ).count(),
            "commitments": len([
                c for c in p.commitments if c.status == "open"
            ]),
            "overdue": len([
                c for c in p.commitments
                if c.status == "open" and c.due_date and c.due_date < insights.today()
            ]),
        }
        for p in people
    }
    return render("crm/people_list.html", people=people, load=load, insights=insights)


@app.route("/people", method="POST", name="people_create")
@require_login
def people_create():
    name = (request.forms.get("name") or "").strip()
    if not name:
        flash("A person needs a name.", "error")
        redirect(url_for("people_list"))
    person = Person.create(
        name=name,
        role=(request.forms.get("role") or "").strip(),
        email=(request.forms.get("email") or "").strip(),
    )
    search.index_entity(person)
    flash(f"Added {person.name}.", "success")
    redirect(url_for("people_list"))


@app.route("/people/<person_id:int>", method="POST", name="person_update")
@require_login
def person_update(person_id: int):
    person = Person.get_or_none(Person.id == person_id)
    if person is None:
        flash("That person doesn't exist.", "error")
        redirect(url_for("people_list"))
    person.name = (request.forms.get("name") or person.name).strip()
    person.role = (request.forms.get("role") or "").strip()
    person.email = (request.forms.get("email") or "").strip()
    # Deactivate rather than delete: their name is on commitments and
    # decisions that stay true after they've left.
    person.active = request.forms.get("active") == "1"
    user_id = parse_int(request.forms.get("user_id"))
    person.user = User.get_or_none(User.id == user_id) if user_id else None
    person.save()
    search.index_entity(person)
    flash(f"Updated {person.name}.", "success")
    redirect(url_for("people_list"))
