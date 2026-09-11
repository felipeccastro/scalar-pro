"""Capture, notes and decisions.

Capture has no page of its own — the box lives at the foot of the dashboard,
and these routes always send the user back there. Three POSTs and no
JavaScript: read the text, review what came out, create what you ticked. The
draft proposal is stored on the Note row rather than in the session, so a
refresh mid-review loses nothing and the review state is a linkable URL.
"""

from __future__ import annotations

import datetime
import json

from bottle import request

from app import app, render
from models import Decision, Note, NoteLink, Person
from pages.core import _load_activity, _load_comments
from utils import current_user, flash, record_activity, redirect, require_login, url_for
import ai
import insights
import search
from pages import capture_extract as extract

# Shown when neither backend answers. Names the two things that fix it, in the
# order someone is likely to try them — an error that doesn't say what to do
# next is just an apology.
NO_BACKEND = (
    "Capture needs an AI backend. Set OPENAI_API_KEY, or start Ollama and try again."
)


@app.route("/capture", method="POST", name="capture_read")
@require_login
def capture_read():
    """Read a pasted note: store the text, ask the model what's in it, and
    send the user back to the dashboard showing the proposal.

    The Note is written *before* the model is called, deliberately. If the
    extraction fails, the words survive — losing someone's meeting notes
    because an API timed out would be a much worse bug than showing them an
    error."""
    body = (request.forms.get("body") or "").strip()
    if not body:
        flash("Paste something first — Capture needs text to read.", "error")
        redirect(url_for("dashboard"))

    author = Person.get_or_none(Person.user == current_user())
    note = Note.create(
        title=_derive_title(body), body=body, author=author,
        occurred_on=datetime.date.today(), created_by=current_user(),
    )
    record_activity("note", note.id, current_user(), "captured")
    search.index_entity(note)

    try:
        proposal = extract.extract(body)
    except ai.LLMError as e:
        # The note is safe; say so, and say what to fix.
        flash(f"{NO_BACKEND} ({e})", "error")
        redirect(url_for("note_detail", note_id=note.id))

    if not extract.count(proposal):
        flash("Nothing to extract from that one — the note is saved.", "info")
        note.captured = True
        note.save()
        redirect(url_for("note_detail", note_id=note.id))

    note.proposal_json = json.dumps(proposal)
    note.save()
    redirect(url_for("dashboard", _query={"review": note.id}))


@app.route("/capture/<note_id:int>/confirm", method="POST", name="capture_confirm")
@require_login
def capture_confirm(note_id: int):
    note = Note.get_or_none(Note.id == note_id)
    if note is None:
        flash("That note doesn't exist.", "error")
        redirect(url_for("dashboard"))
    keys = request.forms.getall("record")
    if not keys:
        flash("Nothing was ticked, so nothing was created.", "info")
        redirect(url_for("dashboard", _query={"review": note.id}))

    result = extract.create_records(note, keys, current_user())
    n = len(result["created"])
    flash(f"Created {n} record{'' if n == 1 else 's'} from your note.", "success")
    # Land on the customer if there is one: seeing the deal, the promises and
    # the note itself sitting together on a real record is the whole point.
    if result["customer"] is not None:
        redirect(url_for("client_detail", client_id=result["customer"].id))
    redirect(url_for("note_detail", note_id=note.id))


@app.route("/capture/<note_id:int>/discard", method="POST", name="capture_discard")
@require_login
def capture_discard(note_id: int):
    """Throw away the proposal, keep the note. Text is never the thing we
    discard."""
    note = Note.get_or_none(Note.id == note_id)
    if note is not None:
        note.proposal_json = ""
        note.captured = True
        note.save()
        flash("Discarded the suggestions. The note is still saved.", "info")
    redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@app.route("/notes", method="GET", name="notes_list")
@require_login
def notes_list():
    q = (request.query.get("q") or "").strip()
    query = Note.select()
    if q:
        query = query.where(Note.title.contains(q) | Note.body.contains(q))
    return render(
        "capture/notes_list.html",
        notes=list(query.order_by(Note.occurred_on.desc(), Note.id.desc())),
        q=q,
    )


@app.route("/notes/<note_id:int>", method="GET", name="note_detail")
@require_login
def note_detail(note_id: int):
    note = Note.get_or_none(Note.id == note_id)
    if note is None:
        flash("That note doesn't exist.", "error")
        redirect(url_for("notes_list"))
    links = list(NoteLink.select().where(NoteLink.note == note).order_by(NoteLink.id))
    return render(
        "capture/note_detail.html",
        note=note,
        links=[{
            "label": insights.subject_label(l.subject_type),
            "name": insights.subject_name(l.subject_type, l.subject_id),
            "url": insights.subject_url(l.subject_type, l.subject_id),
        } for l in links],
        proposal=extract.proposal_rows(note) if note.proposal_json else [],
        comments=_load_comments("note", note.id),
        activity=_load_activity("note", note.id),
        insights=insights,
    )


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@app.route("/decisions", method="GET", name="decisions_list")
@require_login
def decisions_list():
    q = (request.query.get("q") or "").strip()
    query = Decision.select()
    if q:
        query = query.where(
            Decision.title.contains(q) | Decision.decision.contains(q) | Decision.rationale.contains(q)
        )
    return render(
        "capture/decisions_list.html",
        decisions=list(query.order_by(Decision.decided_on.desc(), Decision.id.desc())),
        review_soon={d.id for d in insights.decisions_due_for_review()},
        insights=insights,
        q=q,
    )


def _derive_title(body: str) -> str:
    """A note's title is its first line, trimmed. Asking for one up front
    would add a field to the one box in this app that's meant to accept
    anything you can paste."""
    first = next((line.strip() for line in body.splitlines() if line.strip()), "Note")
    return first[:80] + ("…" if len(first) > 80 else "")
