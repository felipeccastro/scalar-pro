"""Clients (Pro's customers): list, create, detail, update, archive."""

from __future__ import annotations

import datetime

from bottle import request

from app import app, render
from models import CLIENT_STATUSES, Client, Commitment, Decision, Opportunity, Project, Task
from pages._shared import _linked_notes, _load_activity, _load_attachments, _load_comments
from utils import current_user, flash, record_activity, redirect, url_for
import insights
import search


@app.route("/clients", method="GET", name="clients_list")
def clients_list():
    q = (request.query.get("q") or "").strip()
    query = Client.select().where(Client.archived_at.is_null(True))
    if q:
        query = query.where(
            Client.name.contains(q) | Client.email.contains(q) | Client.company.contains(q)
        )
    clients = list(query.order_by(Client.created_at.desc()))
    return render("clients_list.html", clients=clients, statuses=CLIENT_STATUSES, q=q)


@app.route("/clients", method="POST", name="clients_create")
def clients_create():
    name = (request.forms.get("name") or "").strip()
    if not name:
        flash("A client needs a name.", "error")
        redirect(url_for("clients_list"))
    client = Client.create(
        name=name,
        email=(request.forms.get("email") or "").strip(),
        phone=(request.forms.get("phone") or "").strip(),
        company=(request.forms.get("company") or "").strip(),
        status=request.forms.get("status") or "lead",
        notes=(request.forms.get("notes") or "").strip(),
        created_by=current_user(),
    )
    record_activity("client", client.id, current_user(), "created")
    search.index_entity(client)
    flash(f"Added {client.name}.", "success")
    redirect(url_for("client_detail", client_id=client.id))


@app.route("/clients/<client_id:int>", method="GET", name="client_detail")
def client_detail(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is None:
        flash("That client doesn't exist.", "error")
        redirect(url_for("clients_list"))
    tasks = list(
        Task.select().where((Task.client == client) & (Task.archived_at.is_null(True))).order_by(Task.position)
    )
    # Everything else this customer touches. This page is where Capture's
    # payoff lands — paste a meeting note, confirm, come back here, and the
    # deal, the promises and the note itself are all sitting on the record.
    return render(
        "client_detail.html",
        client=client,
        tasks=tasks,
        statuses=CLIENT_STATUSES,
        opportunities=list(
            Opportunity.select()
            .where((Opportunity.customer == client) & Opportunity.archived_at.is_null(True))
            .order_by(Opportunity.value.desc())
        ),
        projects=list(
            Project.select()
            .where((Project.customer == client) & Project.archived_at.is_null(True))
            .order_by(Project.due_date)
        ),
        commitments=list(
            Commitment.select().where(Commitment.customer == client).order_by(Commitment.due_date)
        ),
        decisions=list(
            Decision.select().where(Decision.customer == client).order_by(Decision.decided_on.desc())
        ),
        notes=_linked_notes("client", client.id),
        insights=insights,
        comments=_load_comments("client", client.id),
        attachments=_load_attachments("client", client.id),
        activity=_load_activity("client", client.id),
    )


@app.route("/clients/<client_id:int>", method="POST", name="client_update")
def client_update(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is None:
        flash("That client doesn't exist.", "error")
        redirect(url_for("clients_list"))
    client.name = (request.forms.get("name") or client.name).strip()
    client.email = (request.forms.get("email") or "").strip()
    client.phone = (request.forms.get("phone") or "").strip()
    client.company = (request.forms.get("company") or "").strip()
    client.status = request.forms.get("status") or client.status
    client.notes = (request.forms.get("notes") or "").strip()
    client.updated_at = datetime.datetime.now()
    client.save()
    record_activity("client", client.id, current_user(), "updated")
    search.index_entity(client)
    flash("Client updated.", "success")
    redirect(url_for("client_detail", client_id=client.id))


@app.route("/clients/<client_id:int>/archive", method="POST", name="client_archive")
def client_archive(client_id: int):
    client = Client.select().where(Client.id == client_id).first()
    if client is not None:
        client.archived_at = datetime.datetime.now()
        client.save()
        record_activity("client", client.id, current_user(), "archived")
        flash(f"Archived {client.name}.", "success")
    redirect(url_for("clients_list"))
