"""Operations: projects and the promises people made.

Tasks stay in pages/core.py (they're Core's, and the kanban board belongs
with them). This file owns the two things that make tasks legible at
company scale: the project they belong to, and the commitment someone made
about them.
"""

from __future__ import annotations

import datetime

from bottle import request

from app import app, render
from models import (
    Commitment,
    COMMITMENT_STATUSES,
    Project,
    PROJECT_STATUSES,
    Task,
)
from pages.core import (
    _active_clients,
    _linked_notes,
    _load_activity,
    _load_attachments,
    _load_comments,
    _open_projects,
    _people,
)
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


def _touch(project: Project) -> None:
    """See the note on crm._touch — same contract, same reason."""
    now = datetime.datetime.now()
    project.last_activity_at = now
    project.updated_at = now


@app.route("/projects", method="GET", name="projects_list")
@require_login
def projects_list():
    q = (request.query.get("q") or "").strip()
    query = Project.select().where(Project.archived_at.is_null(True))
    if q:
        query = query.where(Project.name.contains(q) | Project.description.contains(q))
    projects = list(query.order_by(Project.due_date.asc(nulls="LAST"), Project.name))
    # Each row carries its own task arithmetic, because "due Friday" means
    # nothing without "and seven tasks are still open".
    progress = {p.id: _progress(p) for p in projects}
    return render(
        "ops/projects_list.html",
        projects=projects,
        progress=progress,
        statuses=PROJECT_STATUSES,
        clients=_active_clients(),
        people=_people(),
        insights=insights,
        q=q,
    )


def _progress(project: Project) -> dict:
    tasks = list(Task.select().where((Task.project == project) & Task.archived_at.is_null(True)))
    open_tasks = [t for t in tasks if t.status != "done"]
    return {
        "total": len(tasks),
        "open": len(open_tasks),
        "done": len(tasks) - len(open_tasks),
        "overdue": len([t for t in open_tasks if t.due_date and t.due_date < insights.today()]),
    }


@app.route("/projects", method="POST", name="projects_create")
@require_login
def projects_create():
    name = (request.forms.get("name") or "").strip()
    if not name:
        flash("A project needs a name.", "error")
        redirect(url_for("projects_list"))
    project = Project.create(
        name=name,
        description=(request.forms.get("description") or "").strip(),
        status=request.forms.get("status") or "planning",
        owner=parse_int(request.forms.get("owner_id")),
        customer=parse_int(request.forms.get("customer_id")),
        due_date=parse_date(request.forms.get("due_date")),
        created_by=current_user(),
    )
    record_activity("project", project.id, current_user(), "created")
    search.index_entity(project)
    flash(f"Added {project.name}.", "success")
    redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<project_id:int>", method="GET", name="project_detail")
@require_login
def project_detail(project_id: int):
    project = Project.get_or_none(Project.id == project_id)
    if project is None:
        flash("That project doesn't exist.", "error")
        redirect(url_for("projects_list"))
    return render(
        "ops/project_detail.html",
        project=project,
        progress=_progress(project),
        statuses=PROJECT_STATUSES,
        clients=_active_clients(),
        people=_people(),
        tasks=list(
            Task.select()
            .where((Task.project == project) & Task.archived_at.is_null(True))
            .order_by(Task.status, Task.due_date.asc(nulls="LAST"))
        ),
        commitments=list(
            Commitment.select().where(Commitment.project == project).order_by(Commitment.due_date)
        ),
        notes=_linked_notes("project", project.id),
        comments=_load_comments("project", project.id),
        attachments=_load_attachments("project", project.id),
        activity=_load_activity("project", project.id),
        insights=insights,
    )


@app.route("/projects/<project_id:int>", method="POST", name="project_update")
@require_login
def project_update(project_id: int):
    project = Project.get_or_none(Project.id == project_id)
    if project is None:
        flash("That project doesn't exist.", "error")
        redirect(url_for("projects_list"))
    old_status = project.status
    project.name = (request.forms.get("name") or project.name).strip()
    project.description = (request.forms.get("description") or "").strip()
    project.status = request.forms.get("status") or project.status
    project.owner = parse_int(request.forms.get("owner_id"))
    project.customer = parse_int(request.forms.get("customer_id"))
    project.due_date = parse_date(request.forms.get("due_date"))
    _touch(project)
    project.save()
    if project.status != old_status:
        record_activity(
            "project", project.id, current_user(), "status_changed",
            old=old_status, new=project.status,
        )
    else:
        record_activity("project", project.id, current_user(), "updated")
    search.index_entity(project)
    flash("Project updated.", "success")
    redirect(url_for("project_detail", project_id=project.id))


@app.route("/projects/<project_id:int>/archive", method="POST", name="project_archive")
@require_login
def project_archive(project_id: int):
    project = Project.get_or_none(Project.id == project_id)
    if project is not None:
        project.archived_at = datetime.datetime.now()
        project.save()
        record_activity("project", project.id, current_user(), "archived")
        flash(f"Archived {project.name}.", "success")
    redirect(url_for("projects_list"))


# ---------------------------------------------------------------------------
# Commitments
# ---------------------------------------------------------------------------

# ?filter= values. "overdue" is here and not in the model for the reason
# explained on the Commitment class: it's a question about today, not a
# property of the row.
FILTERS = ("open", "overdue", "mine", "done", "all")


@app.route("/commitments", method="GET", name="commitments_list")
@require_login
def commitments_list():
    active = request.query.get("filter") or "open"
    if active not in FILTERS:
        active = "open"
    query = Commitment.select()
    if active == "open":
        query = query.where(Commitment.status == "open")
    elif active == "overdue":
        query = query.where(
            (Commitment.status == "open")
            & Commitment.due_date.is_null(False)
            & (Commitment.due_date < insights.today())
        )
    elif active == "done":
        query = query.where(Commitment.status == "done")
    elif active == "mine":
        me = _current_person()
        query = query.where((Commitment.person == me) if me else (Commitment.id < 0))
    commitments = list(query.order_by(Commitment.due_date.asc(nulls="LAST"), Commitment.id))
    return render(
        "ops/commitments_list.html",
        commitments=commitments,
        active=active,
        filters=FILTERS,
        statuses=COMMITMENT_STATUSES,
        clients=_active_clients(),
        projects=_open_projects(),
        people=_people(),
        insights=insights,
    )


def _current_person():
    from models import Person

    return Person.get_or_none(Person.user == current_user())


@app.route("/commitments", method="POST", name="commitments_create")
@require_login
def commitments_create():
    description = (request.forms.get("description") or "").strip()
    if not description:
        flash("A commitment needs a description — what did someone promise?", "error")
        redirect(url_for("commitments_list"))
    commitment = Commitment.create(
        description=description,
        person=parse_int(request.forms.get("person_id")),
        due_date=parse_date(request.forms.get("due_date")),
        status=request.forms.get("status") or "open",
        source=request.forms.get("source") or "manual",
        customer=parse_int(request.forms.get("customer_id")),
        project=parse_int(request.forms.get("project_id")),
        created_by=current_user(),
    )
    record_activity("commitment", commitment.id, current_user(), "created")
    search.index_entity(commitment)
    flash("Commitment recorded.", "success")
    redirect(url_for("commitments_list"))


@app.route("/commitments/<commitment_id:int>/status", method="POST", name="commitment_status")
@require_login
def commitment_status(commitment_id: int):
    """One-click done/reopen from the list. Kept separate from a full update
    so the common action — ticking something off — is one button and not a
    form."""
    commitment = Commitment.get_or_none(Commitment.id == commitment_id)
    if commitment is None:
        flash("That commitment doesn't exist.", "error")
        redirect(url_for("commitments_list"))
    status = request.forms.get("status") or "done"
    if status not in COMMITMENT_STATUSES:
        status = "done"
    old = commitment.status
    commitment.status = status
    commitment.updated_at = datetime.datetime.now()
    commitment.save()
    record_activity(
        "commitment", commitment.id, current_user(), "status_changed", old=old, new=status
    )
    flash("Marked done." if status == "done" else "Reopened.", "success")
    redirect(url_for("commitments_list", _query={"filter": request.forms.get("back") or "open"}))
