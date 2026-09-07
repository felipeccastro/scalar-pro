"""The two screens that read the whole company back to you.

`/` is the CEO briefing: what needs attention, five numbers, what changed,
what we decided. `/cracks` is the same data asked a harsher question — what's
late, at risk, quiet, or ownerless.

Neither page computes anything itself. Every list comes from insights.py,
which is the only reason the dashboard's "3 overdue commitments" and the
control center's overdue list can't drift apart.
"""

from __future__ import annotations

import datetime

from bottle import request

from app import app, render
from models import Note
from utils import require_login, current_user
import insights


def _capture_panel() -> dict:
    """State for the Capture box at the foot of the dashboard.

    Three states, one panel: an empty prompt, a proposal awaiting review, or
    the prompt again after the proposal was accepted. The review state is
    addressed by `?review=<note_id>` rather than held in the session, so it
    survives a refresh and can be linked to.
    """
    note_id = request.query.get("review")
    if not note_id or not note_id.isdigit():
        return {"note": None, "proposal": None}
    note = Note.get_or_none(Note.id == int(note_id))
    if note is None or not note.proposal_json:
        return {"note": None, "proposal": None}
    # Imported here rather than at module scope: modules/capture registers
    # after this one, and only this function needs it.
    from modules.capture.extract import proposal_rows

    return {"note": note, "proposal": proposal_rows(note)}


@app.route("/", method="GET", name="dashboard")
@require_login
def dashboard():
    user = current_user()
    attention = insights.attention_rows()
    return render(
        "dashboard/dashboard.html",
        greeting=_greeting(),
        first_name=(user.name or "").split(" ")[0] if user else "",
        attention=attention,
        standfirst=_standfirst(len(attention)),
        snapshot=insights.snapshot(),
        changed=insights.whats_changed(),
        recent_decisions=insights.recent_decisions(),
        review_decisions=insights.decisions_due_for_review(),
        capture=_capture_panel(),
        insights=insights,
    )


@app.route("/cracks", method="GET", name="cracks")
@require_login
def cracks():
    return render(
        "dashboard/cracks.html",
        overdue=insights.overdue_rows(),
        at_risk=insights.at_risk_projects(),
        stalled=insights.stalled_rows(),
        unassigned=insights.unassigned_rows(),
        commitments=insights.commitments_this_week(),
        insights=insights,
    )


def _greeting() -> str:
    """Morning/afternoon/evening. A briefing that says "good morning" at 9pm
    reads as generated rather than written."""
    hour = datetime.datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def _standfirst(count: int) -> str:
    if count == 0:
        return "Nothing needs your attention. That is either very good news or a sign nobody is writing anything down."
    words = {1: "One thing", 2: "Two things", 3: "Three things", 4: "Four things",
             5: "Five things", 6: "Six things"}.get(count, f"{count} things")
    return f"{words} need{'s' if count == 1 else ''} your attention."
