"""Tasks: the kanban board — list/reorder, create, detail, update, archive."""

from __future__ import annotations

import datetime

from bottle import request, response

from app import app, render
from models import TASK_PRIORITIES, TASK_STATUSES, Person, Task, User
from pages._shared import (
    _active_clients,
    _linked_notes,
    _load_activity,
    _load_attachments,
    _load_comments,
    _open_projects,
    _people,
)
from utils import abort, current_user, flash, notify, parse_date, parse_int, record_activity, redirect, url_for
import insights
import search


@app.route("/tasks", method="GET", name="tasks_list")
def tasks_list():
    q = (request.query.get("q") or "").strip()
    query = Task.select().where(Task.archived_at.is_null(True))
    if q:
        query = query.where(Task.title.contains(q) | Task.description.contains(q))
    tasks = list(query.order_by(Task.position, Task.id))
    grouped = {s: [t for t in tasks if t.status == s] for s in TASK_STATUSES}
    return render(
        "tasks_list.html",
        grouped=grouped,
        statuses=TASK_STATUSES,
        priorities=TASK_PRIORITIES,
        clients=_active_clients(),
        people=_people(),
        projects=_open_projects(),
        insights=insights,
        q=q,
    )


def _assignee_for(person: Person | None) -> User | None:
    """Core's notification path keys off Task.assignee (a User), but Pro's
    forms pick an owner (a Person). Mirror the choice across when that person
    has an account, so reassignment still notifies someone; when they don't,
    the task simply has an owner and no notification, which is the honest
    outcome."""
    if person is None or person.user_id is None:
        return None
    return person.user


@app.route("/tasks", method="POST", name="tasks_create")
def tasks_create():
    title = (request.forms.get("title") or "").strip()
    if not title:
        flash("A task needs a title.", "error")
        redirect(url_for("tasks_list"))
    owner = Person.get_or_none(Person.id == parse_int(request.forms.get("owner_id")))
    assignee = _assignee_for(owner)
    last = Task.select().order_by(Task.position.desc()).first()
    task = Task.create(
        title=title,
        description=(request.forms.get("description") or "").strip(),
        status=request.forms.get("status") or "todo",
        client=parse_int(request.forms.get("client_id")),
        project=parse_int(request.forms.get("project_id")),
        owner=owner,
        assignee=assignee,
        due_date=parse_date(request.forms.get("due_date")),
        priority=request.forms.get("priority") or "normal",
        position=(last.position + 1) if last else 0,
        created_by=current_user(),
    )
    record_activity("task", task.id, current_user(), "created")
    search.index_entity(task)
    if task.assignee_id and task.assignee_id != current_user().id:
        notify(task.assignee, "assignment", task_id=task.id, task_title=task.title)
    flash(f"Added “{task.title}”.", "success")
    redirect(url_for("task_detail", task_id=task.id))


@app.route("/tasks/<task_id:int>", method="GET", name="task_detail")
def task_detail(task_id: int):
    task = Task.select().where(Task.id == task_id).first()
    if task is None:
        flash("That task doesn't exist.", "error")
        redirect(url_for("tasks_list"))
    return render(
        "task_detail.html",
        task=task,
        statuses=TASK_STATUSES,
        priorities=TASK_PRIORITIES,
        clients=_active_clients(),
        people=_people(),
        projects=_open_projects(),
        notes=_linked_notes("task", task.id),
        comments=_load_comments("task", task.id),
        attachments=_load_attachments("task", task.id),
        activity=_load_activity("task", task.id),
    )


@app.route("/tasks/<task_id:int>", method="POST", name="task_update")
def task_update(task_id: int):
    task = Task.select().where(Task.id == task_id).first()
    if task is None:
        flash("That task doesn't exist.", "error")
        redirect(url_for("tasks_list"))
    old_status = task.status
    task.title = (request.forms.get("title") or task.title).strip()
    task.description = (request.forms.get("description") or "").strip()
    task.status = request.forms.get("status") or task.status
    task.client = parse_int(request.forms.get("client_id"))
    task.project = parse_int(request.forms.get("project_id"))
    task.due_date = parse_date(request.forms.get("due_date"))
    task.priority = request.forms.get("priority") or task.priority
    owner = Person.get_or_none(Person.id == parse_int(request.forms.get("owner_id")))
    task.owner = owner
    new_assignee = _assignee_for(owner)
    new_assignee_id = new_assignee.id if new_assignee else None
    reassigned = new_assignee_id and new_assignee_id != task.assignee_id
    task.assignee = new_assignee
    task.updated_at = datetime.datetime.now()
    task.save()
    if task.status != old_status:
        record_activity("task", task.id, current_user(), "status_changed", old=old_status, new=task.status)
    else:
        record_activity("task", task.id, current_user(), "updated")
    search.index_entity(task)
    if reassigned and task.assignee_id != current_user().id:
        notify(task.assignee, "assignment", task_id=task.id, task_title=task.title)
    flash("Task updated.", "success")
    redirect(url_for("task_detail", task_id=task.id))


@app.route("/tasks/reorder", method="POST", name="tasks_reorder")
def tasks_reorder():
    """Drag-and-drop endpoint for the /tasks board (see the script at the
    bottom of tasks_list.html). The board sends the *entire*, freshly
    dropped ordering of one column: every listed task gets that column's
    status and a position matching its index in the list. Only ever
    touches status/position — never title/description/etc. — so a drop
    can't clobber anything else about a task.
    """
    status = request.forms.get("status") or ""
    if status not in TASK_STATUSES:
        abort(400, "That's not a valid status.")
    task_ids = [int(v) for v in request.forms.getall("task_id") if v.isdigit()]
    tasks_by_id = {t.id: t for t in Task.select().where(Task.id.in_(task_ids))}
    for position, task_id in enumerate(task_ids):
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        old_status = task.status
        task.status = status
        task.position = position
        task.updated_at = datetime.datetime.now()
        task.save()
        if old_status != status:
            record_activity("task", task.id, current_user(), "status_changed", old=old_status, new=status)
    response.status = 204
    return ""


@app.route("/tasks/<task_id:int>/archive", method="POST", name="task_archive")
def task_archive(task_id: int):
    task = Task.select().where(Task.id == task_id).first()
    if task is not None:
        task.archived_at = datetime.datetime.now()
        task.save()
        record_activity("task", task.id, current_user(), "archived")
        flash(f"Archived “{task.title}”.", "success")
    redirect(url_for("tasks_list"))
