"""Query/redirect helpers used by more than one route module — kept here
instead of duplicated or hung off whichever module happened to need one
first. Also what pages/dashboard.py, crm.py, ops.py and capture.py import
(`from pages._shared import ...`) for the same reason: one query,
used everywhere it's needed, rather than each page file running its own.
"""

from __future__ import annotations

from models import Activity, Attachment, Client, Comment, Note, NoteLink, Person, Project
from utils import redirect
import insights


def _load_comments(subject_type: str, subject_id: int) -> list[Comment]:
    return list(
        Comment.select()
        .where((Comment.subject_type == subject_type) & (Comment.subject_id == subject_id))
        .order_by(Comment.created_at)
    )


def _load_attachments(subject_type: str, subject_id: int) -> list[Attachment]:
    return list(
        Attachment.select()
        .where((Attachment.subject_type == subject_type) & (Attachment.subject_id == subject_id))
        .order_by(Attachment.created_at.desc())
    )


def _linked_notes(subject_type: str, subject_id: int) -> list[Note]:
    """The notes a record was extracted from — the other half of Capture's
    promise that structure never replaces the text it came from."""
    return list(
        Note.select()
        .join(NoteLink)
        .where((NoteLink.subject_type == subject_type) & (NoteLink.subject_id == subject_id))
        .order_by(Note.created_at.desc())
    )


def _load_activity(subject_type: str, subject_id: int, limit: int = 20) -> list[Activity]:
    return list(
        Activity.select()
        .where((Activity.subject_type == subject_type) & (Activity.subject_id == subject_id))
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )


def _people() -> list[Person]:
    """Everyone who can be handed a piece of work. Shared by every owner
    select in the app — Pro's page files import this rather than each running
    their own query, so the options are identical everywhere."""
    return list(Person.select().where(Person.active == True).order_by(Person.name))  # noqa: E712


def _open_projects() -> list[Project]:
    return list(
        Project.select()
        .where(Project.archived_at.is_null(True) & (Project.status != "done"))
        .order_by(Project.name)
    )


def _active_clients() -> list[Client]:
    return list(Client.select().where(Client.archived_at.is_null(True)).order_by(Client.name))


def _redirect_to_subject(subject_type: str, subject_id: int):
    """Back to whatever was just commented on / attached to. The type-to-route
    table lives in insights.SUBJECT_REGISTRY, which is also what the activity
    feed and the note's linked-records list resolve through — one table, so a
    new commentable record type is a single entry rather than three."""
    redirect(insights.subject_url(subject_type, subject_id))
