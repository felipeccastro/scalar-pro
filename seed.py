"""The fictional company a fresh Pro instance wakes up as.

Two rules shape this file.

**Every date is relative to today.** Nothing here is a literal calendar date,
because a demo whose "overdue" commitments went overdue in March reads as a
bug. `d(-6)` is six days ago, `d(3)` is Thursday-ish. Seed it today or seed it
next year; the dashboard says the same thing.

**The company is deliberately in trouble.** An immaculate dataset produces an
empty dashboard, which is a worse demo than no dashboard. So: commitments are
late, two deals have gone quiet, a project is blocked, several tasks have no
owner, and a pricing decision is due for review. Every one of those exists to
light up a specific section of the dashboard or the control center — if you
change the numbers here, check both pages still have something to say.

Run standalone to reseed a scratch database:

    SQLITE_PATH=/tmp/scratch.db python3 seed.py
"""

from __future__ import annotations

import datetime
import json

from models import (
    Activity,
    Client,
    Commitment,
    Decision,
    Note,
    NoteLink,
    Opportunity,
    Person,
    Project,
    Task,
    User,
)

TODAY = datetime.date.today


def d(offset: int) -> datetime.date:
    """A date `offset` days from today. Negative is the past."""
    return TODAY() + datetime.timedelta(days=offset)


def ts(days_ago: float, hour: int = 10) -> datetime.datetime:
    """A timestamp `days_ago` days back, at a plausible hour of the working
    day — so the activity feed groups into real days instead of all landing at
    whatever o'clock the seed happened to run."""
    day = TODAY() - datetime.timedelta(days=int(days_ago))
    return datetime.datetime.combine(day, datetime.time(hour, 0))


# ---------------------------------------------------------------------------
# The cast
# ---------------------------------------------------------------------------

# (name, role, email). The first slot is replaced by the registering owner —
# it's their company, so they should be in it.
PEOPLE = [
    ("Maria Okonkwo", "COO", "maria@meridian.example"),
    ("João Ferreira", "Engineering lead", "joao@meridian.example"),
    ("Priya Raman", "Head of Sales", "priya@meridian.example"),
    ("Tomas Lindqvist", "Account executive", "tomas@meridian.example"),
    ("Dana Whitfield", "Customer success", "dana@meridian.example"),
    ("Sam Ortega", "Product designer", "sam@meridian.example"),
    ("Ruth Nakamura", "Finance & operations", "ruth@meridian.example"),
]

# (name, status, company-ish notes)
CUSTOMERS = [
    ("Acme Corp", "active", "Longest-running account. Monthly retainer, renews in the spring."),
    ("Northwind Traders", "lead", "Introduced by Priya's old colleague. Still evaluating."),
    ("Vertex Logistics", "active", "Rolled out to three depots. Wants the rest by Q3."),
    ("Bluepeak Health", "lead", "Procurement is slow — expect a security review."),
    ("Cobalt Robotics", "active", "Heavy API users. Watch their rate limits."),
    ("Harbourline Freight", "active", "Renewed without a fuss last time."),
    ("Sundial Media", "inactive", "Went quiet after their round fell through."),
    ("Iron Oak Manufacturing", "active", "Old-school buyer. Prefers a phone call to a doc."),
    ("Palisade Legal", "lead", "Needs an audit trail before they'll sign anything."),
    ("Kestrel Analytics", "active", "Champion is their VP Eng, not procurement."),
    ("Riverstone Dental Group", "active", "Twelve practices, one admin. Keep it simple for them."),
    ("Meadowlark Foods", "lead", "Met at the trade show. Warm but unqualified."),
    ("Quanta Semiconductor", "lead", "Biggest logo in the pipeline. Long cycle."),
    ("Fairfield Schools Trust", "inactive", "Budget cycle ended. Revisit in the autumn."),
    ("Lumen Energy", "active", "Expanded twice on their own. Low touch."),
]

# (title, customer, value, stage, owner, next_action, next_action_due, days_quiet)
OPPORTUNITIES = [
    ("Enterprise rollout", "Quanta Semiconductor", 180_000, "negotiation", "Priya Raman",
     "Send the redlined contract", -3, 2),
    ("Platform licence", "Northwind Traders", 64_000, "proposal", "Tomas Lindqvist",
     "Follow up on the proposal", -8, 14),
    ("Depot expansion", "Vertex Logistics", 95_000, "negotiation", "Priya Raman",
     "Agree the phase-two timeline", 0, 1),
    ("Security review + pilot", "Bluepeak Health", 42_000, "qualified", "Tomas Lindqvist",
     "Chase the security questionnaire", -1, 5),
    ("Audit trail add-on", "Palisade Legal", 28_000, "proposal", "Priya Raman",
     "Book the compliance walkthrough", 4, 3),
    ("Multi-practice plan", "Riverstone Dental Group", 36_000, "qualified", "Dana Whitfield",
     "Scope the migration", 6, 4),
    ("API tier upgrade", "Cobalt Robotics", 55_000, "negotiation", None,
     "Confirm the volume discount", 2, 12),
    ("Trade show follow-up", "Meadowlark Foods", 18_000, "lead", "Tomas Lindqvist",
     "Qualify the budget", 9, 6),
    ("Renewal + expansion", "Harbourline Freight", 72_000, "won", "Priya Raman", "", None, 20),
    ("Regional pilot", "Sundial Media", 25_000, "lost", "Tomas Lindqvist", "", None, 45),
]

# (name, status, owner, customer, due_in_days, days_quiet, description)
PROJECTS = [
    ("Vertex depot rollout — phase two", "at_risk", "Maria Okonkwo", "Vertex Logistics", 4, 3,
     "Three depots live, four to go. Hardware is the long pole."),
    ("SSO and SCIM", "blocked", "João Ferreira", None, 11, 9,
     "Blocked on a decision about which identity providers we commit to supporting."),
    ("Billing rewrite", "active", "Ruth Nakamura", None, 38, 2,
     "Move off the hand-rolled invoice generator before renewals season."),
    ("Website redesign", "active", "Sam Ortega", None, 5, 1,
     "New marketing site plus a real pricing page."),
    ("Riverstone migration", "planning", None, "Riverstone Dental Group", 26, 16,
     "Twelve practices onto one account. Needs a data map first."),
    ("Q2 security audit", "done", "João Ferreira", None, -12, 12,
     "External pen test and remediation. Closed out clean."),
]

# (description, person, due_in_days, status, source, customer, project)
COMMITMENTS = [
    ("Investigate whether SSO can ship before the Quanta signature", "João Ferreira", -9, "open",
     "meeting", "Quanta Semiconductor", "SSO and SCIM"),
    ("Send Northwind the revised proposal", "Tomas Lindqvist", -6, "open", "meeting",
     "Northwind Traders", None),
    ("Get the Vertex hardware order confirmed", "Maria Okonkwo", -4, "open", "email",
     "Vertex Logistics", "Vertex depot rollout — phase two"),
    ("Write up the pricing decision for the team", None, -2, "open", "meeting", None, None),
    ("Reply to Bluepeak's security questionnaire", "Dana Whitfield", -1, "open", "email",
     "Bluepeak Health", None),
    ("Share the phase-two timeline with Vertex", "Priya Raman", 0, "open", "meeting",
     "Vertex Logistics", "Vertex depot rollout — phase two"),
    ("Draft the Quanta contract redlines", "Priya Raman", 1, "open", "meeting",
     "Quanta Semiconductor", None),
    ("Book the Palisade compliance walkthrough", "Tomas Lindqvist", 3, "open", "email",
     "Palisade Legal", None),
    ("Ship the pricing page copy", "Sam Ortega", 4, "open", "meeting", None, "Website redesign"),
    ("Reconcile March invoices before the rewrite", "Ruth Nakamura", 5, "open", "manual", None,
     "Billing rewrite"),
    ("Scope the Riverstone data migration", None, 7, "open", "meeting",
     "Riverstone Dental Group", "Riverstone migration"),
    ("Send Acme their quarterly summary", "Dana Whitfield", -3, "done", "manual", "Acme Corp", None),
    ("Close out the security audit findings", "João Ferreira", -8, "done", "meeting", None,
     "Q2 security audit"),
    ("Introduce Kestrel to the new success owner", "Dana Whitfield", -5, "done", "email",
     "Kestrel Analytics", None),
    ("Chase Fairfield about next year's budget", "Tomas Lindqvist", -14, "cancelled", "manual",
     "Fairfield Schools Trust", None),
]

# (title, decision, rationale, owner, decided_days_ago, review_in_days, status, project)
DECISIONS = [
    ("Launch the enterprise plan in October",
     "We ship the enterprise tier on 1 October, with SSO as the headline feature.",
     "Three of our four largest deals are gated on SSO. October is the last window before "
     "buyers freeze budgets for the year.",
     None, 3, 21, "decided", None),
    ("Support Okta and Entra only, at first",
     "SSO ships supporting Okta and Microsoft Entra. Everything else waits.",
     "Those two cover every customer currently asking. Supporting SAML generically doubled "
     "João's estimate for no named customer.",
     "João Ferreira", 6, 5, "under_review", "SSO and SCIM"),
    ("Raise list price by 12%",
     "List price goes up 12% on new business from next month. Existing customers are held "
     "for one renewal cycle.",
     "We have not revisited pricing in two years and win rates are above target, which "
     "usually means we are cheap rather than good.",
     None, 9, -2, "under_review", None),
    ("Rewrite billing rather than buy",
     "Build the new invoicing service in-house instead of moving to a vendor.",
     "Our usage model doesn't fit any of the three vendors we trialled, and the workarounds "
     "cost more than the rewrite.",
     "Ruth Nakamura", 14, 60, "decided", "Billing rewrite"),
    ("One customer success owner per account",
     "Every account gets a single named success owner, listed on the customer record.",
     "Two customers escalated last quarter after falling between two people. The fix is "
     "cheap and the failure mode is expensive.",
     "Dana Whitfield", 18, None, "decided", None),
    ("Pause the Fairfield opportunity",
     "Stop active work on Fairfield Schools Trust until their next budget cycle.",
     "Their budget closed and no amount of follow-up changes that. Revisit in the autumn.",
     "Tomas Lindqvist", 22, 75, "decided", None),
    ("Hire a second engineer before a second designer",
     "The next hire is backend. Design stays at one until the new year.",
     "Delivery, not design, is what's holding up the roadmap right now.",
     None, 27, 30, "decided", None),
    ("Keep the free tier",
     "The free tier stays, unchanged, through the enterprise launch.",
     "It's our main source of qualified leads and killing it to protect a price rise would "
     "cost more pipeline than it saves.",
     None, 34, None, "decided", None),
    ("Move the depot rollout to phased delivery",
     "Vertex gets three depots now and four in phase two, rather than all seven at once.",
     "Hardware lead times made a single cutover impossible without slipping the whole thing "
     "by a quarter.",
     "Maria Okonkwo", 41, None, "superseded", "Vertex depot rollout — phase two"),
    ("Run the security audit externally",
     "Bring in an outside firm for the annual audit rather than self-assessing.",
     "Enterprise buyers ask for third-party evidence. Self-assessment doesn't unblock a deal.",
     "João Ferreira", 55, None, "decided", "Q2 security audit"),
]

# (title, status, priority, due_in_days | None, person | None, customer | None, project | None)
TASKS = [
    ("Draft the enterprise pricing page", "in_progress", "high", 2, "Sam Ortega", None, "Website redesign"),
    ("Write SSO integration spec", "in_progress", "urgent", -5, "João Ferreira", None, "SSO and SCIM"),
    ("Pick the identity providers to support", "todo", "urgent", -7, None, None, "SSO and SCIM"),
    ("Set up the Okta test tenant", "todo", "high", 3, "João Ferreira", None, "SSO and SCIM"),
    ("Map SCIM fields to our user model", "todo", "normal", 8, None, None, "SSO and SCIM"),
    ("Confirm depot 4 hardware delivery", "in_progress", "urgent", -4, "Maria Okonkwo", "Vertex Logistics", "Vertex depot rollout — phase two"),
    ("Train depot 4 supervisors", "todo", "high", 3, "Dana Whitfield", "Vertex Logistics", "Vertex depot rollout — phase two"),
    ("Depot 5 site survey", "todo", "normal", 9, None, "Vertex Logistics", "Vertex depot rollout — phase two"),
    ("Write the phase-two rollout plan", "todo", "high", -2, "Maria Okonkwo", "Vertex Logistics", "Vertex depot rollout — phase two"),
    ("Close out depot 3 snags", "done", "normal", -6, "Dana Whitfield", "Vertex Logistics", "Vertex depot rollout — phase two"),
    ("Model the new invoice schema", "in_progress", "normal", 12, "Ruth Nakamura", None, "Billing rewrite"),
    ("Export historic invoices for reconciliation", "todo", "normal", 16, "Ruth Nakamura", None, "Billing rewrite"),
    ("Decide on proration rules", "todo", "high", 10, None, None, "Billing rewrite"),
    ("Set up the billing staging environment", "todo", "low", 24, "João Ferreira", None, "Billing rewrite"),
    ("New homepage copy", "in_progress", "normal", 1, "Sam Ortega", None, "Website redesign"),
    ("Case study: Harbourline", "todo", "low", 14, "Dana Whitfield", "Harbourline Freight", "Website redesign"),
    ("Rebuild the nav for mobile", "todo", "normal", 6, "Sam Ortega", None, "Website redesign"),
    ("Kill the old blog templates", "done", "low", -3, "Sam Ortega", None, "Website redesign"),
    ("Data map for the twelve practices", "todo", "high", 11, None, "Riverstone Dental Group", "Riverstone migration"),
    ("Agree a migration window with Riverstone", "todo", "normal", 18, "Dana Whitfield", "Riverstone Dental Group", "Riverstone migration"),
    ("Redline the Quanta contract", "in_progress", "urgent", 1, "Priya Raman", "Quanta Semiconductor", None),
    ("Prepare the Quanta security pack", "todo", "high", -1, "João Ferreira", "Quanta Semiconductor", None),
    ("Rework the Northwind pricing slide", "todo", "urgent", -6, "Tomas Lindqvist", "Northwind Traders", None),
    ("Answer Bluepeak's security questionnaire", "in_progress", "high", -1, "Dana Whitfield", "Bluepeak Health", None),
    ("Book Palisade compliance walkthrough", "todo", "normal", 3, "Tomas Lindqvist", "Palisade Legal", None),
    ("Qualify Meadowlark's budget", "todo", "low", 9, "Tomas Lindqvist", "Meadowlark Foods", None),
    ("Check Cobalt's rate limit headroom", "todo", "normal", 5, None, "Cobalt Robotics", None),
    ("Quarterly review with Acme", "todo", "normal", 7, "Dana Whitfield", "Acme Corp", None),
    ("Renewal paperwork for Harbourline", "done", "normal", -9, "Ruth Nakamura", "Harbourline Freight", None),
    ("Introduce Kestrel to their new owner", "done", "low", -5, "Dana Whitfield", "Kestrel Analytics", None),
    ("Write the pricing decision memo", "todo", "high", -2, None, None, None),
    ("Update the sales deck with new pricing", "todo", "normal", 4, "Priya Raman", None, None),
    ("Post the October launch plan internally", "todo", "normal", 6, None, None, None),
    ("Interview loop for the backend hire", "in_progress", "high", 8, "João Ferreira", None, None),
    ("Write the backend role description", "done", "normal", -4, "Maria Okonkwo", None, None),
    ("Renew the monitoring contract", "todo", "low", 20, "Ruth Nakamura", None, None),
    ("Archive the Sundial workspace", "todo", "low", 15, None, "Sundial Media", None),
    ("Follow up with Fairfield in the autumn", "todo", "low", 60, "Tomas Lindqvist", "Fairfield Schools Trust", None),
    ("Reconcile March invoices", "in_progress", "high", 5, "Ruth Nakamura", None, "Billing rewrite"),
    ("Draft Q3 board update", "todo", "normal", 13, None, None, None),
]

# (title, days_ago, author, tags, body)
NOTES = [
    ("Quanta — negotiation call", 2, "Priya Raman", "quanta,enterprise,sso",
     "Ninety minutes with Quanta's VP Eng and their procurement lead. They're in on the "
     "product. Two things stand between us and signature: SSO, which they treat as "
     "non-negotiable, and a mutual NDA their legal team wants redrafted. Value looks like "
     "$180k for year one across four business units. I said we'd have redlines back this "
     "week. João is checking whether SSO can land before the October launch."),
    ("Vertex — depot 4 slipping", 3, "Maria Okonkwo", "vertex,rollout,risk",
     "Hardware for depot 4 hasn't shipped. The supplier is quoting another two weeks, which "
     "puts the phase-two date at risk. Options: pull depot 5 forward and do 4 last, or hold "
     "the whole phase. I'd rather resequence than slip. Need to tell Vertex either way "
     "before Friday."),
    ("Pricing — where we landed", 4, None, "pricing,decision",
     "Long argument, good outcome. Twelve percent on new business, existing customers held "
     "for one renewal. The thing that convinced everyone was win rate: we're closing well "
     "above target, which almost always means the price is too low rather than the pitch "
     "being too good. Someone needs to write this up properly — right now it exists as a "
     "whiteboard photo."),
    ("Bluepeak security review", 5, "Dana Whitfield", "bluepeak,security",
     "Their infosec team sent a 140-question spreadsheet. Most of it we can answer from the "
     "audit report. About a dozen questions are about data residency and we don't have a "
     "good answer yet. Flagged to João."),
    ("Weekly ops sync", 6, "Maria Okonkwo", "ops,weekly",
     "Vertex is the fire. Billing rewrite is on track and boring, which is what we want. "
     "Two tasks came out of this with nobody's name on them, which is becoming a pattern — "
     "we keep agreeing things are important and not agreeing who does them."),
    ("Northwind — went quiet", 8, "Tomas Lindqvist", "northwind,pipeline",
     "No reply to the proposal in over a week. Not a no, but not a yes. Their champion "
     "mentioned a reorg last time we spoke, which might explain it. Trying a different "
     "contact this week."),
    ("SSO scope conversation", 9, "João Ferreira", "sso,engineering",
     "Generic SAML support roughly doubles the estimate and no customer has actually asked "
     "for it — everyone asking is on Okta or Entra. Proposing we ship those two and treat "
     "anything else as a sales exception. This needs a decision before I start."),
    ("Cobalt — API usage", 10, None, "cobalt,api",
     "Cobalt is at about 80% of their rate limit on a normal weekday and they're adding a "
     "fleet next month. Either they upgrade tier or they start getting 429s in production, "
     "and I'd rather that conversation happen now than at 2am."),
    ("Riverstone kickoff", 12, "Dana Whitfield", "riverstone,migration",
     "Twelve practices, one very patient office manager, twelve slightly different ways of "
     "recording the same thing. This will live or die on the data map. Nobody owns it yet."),
    ("Board prep notes", 13, None, "board",
     "Pipeline is healthy. Delivery is the story: two projects at risk, one blocked on a "
     "decision that's been open nine days. Expect questions about hiring."),
    ("Acme quarterly review", 15, "Dana Whitfield", "acme,renewal",
     "Happy. Usage up, no open tickets, renewal is a formality. Asked about the enterprise "
     "tier out of curiosity rather than need."),
    ("Harbourline renewal signed", 17, "Priya Raman", "harbourline,renewal,won",
     "Signed for another year plus a seat expansion. No negotiation, no discount asked for. "
     "Worth understanding why this one is so easy when others aren't."),
    ("Palisade — audit trail", 19, "Tomas Lindqvist", "palisade,compliance",
     "They cannot sign without an immutable audit trail. We have most of it; what we don't "
     "have is a way to show it to an auditor. That's a week of work, not a quarter."),
    ("Hiring debate", 21, "Maria Okonkwo", "hiring",
     "Backend or design. Went round twice. Delivery is the constraint, so backend wins. "
     "Sam is going to be stretched and we should say that out loud rather than pretend."),
    ("Kestrel handover", 23, "Dana Whitfield", "kestrel",
     "Warm handover to the new success owner. Their VP Eng is the real champion — "
     "procurement barely engages, which is fine until renewal."),
    ("Sundial — closing it out", 26, "Tomas Lindqvist", "sundial,lost",
     "Their round fell through and the project is shelved. Marking it lost rather than "
     "leaving it rotting in the pipeline. Worth a call in six months."),
    ("Billing vendor trials", 29, "Ruth Nakamura", "billing,vendors",
     "Trialled three. All three assume you bill per seat per month. We bill on a usage "
     "metric that none of them model, so every one of them needs a workaround that costs "
     "more than building it ourselves."),
    ("Iron Oak check-in", 32, "Dana Whitfield", "ironoak",
     "Phone call, not a doc — that's how they like it. Everything fine. They will never use "
     "the in-app messaging and that's OK."),
    ("Security audit — findings", 36, "João Ferreira", "security,audit",
     "Nine findings, none critical, all closed. The report is the thing enterprise buyers "
     "actually want to see, and now we have one."),
    ("Trade show debrief", 40, "Tomas Lindqvist", "events,pipeline",
     "Forty conversations, maybe six worth following up, one genuinely good (Meadowlark). "
     "The booth cost more than the pipeline it generated, but the two customer conversations "
     "we had were worth the trip on their own."),
]


# ---------------------------------------------------------------------------


def _by_name(rows: dict, name: str | None):
    return rows.get(name) if name else None


def seed_demo_data(owner: User) -> None:
    """Populate a fresh instance. Called once, right after the first owner
    registers (pages.py: register_owner_submit) — not from ensure_schema(),
    since it needs a real User to attribute rows to.

    Idempotent by refusing to run twice: if there's already a Person, assume
    the instance has been seeded and leave it alone."""
    if Person.select().exists():
        return

    people: dict[str, Person] = {}
    me = Person.create(name=owner.name, role="CEO", email=owner.email, user=owner)
    people[owner.name] = me
    for name, role, email in PEOPLE:
        people[name] = Person.create(name=name, role=role, email=email)

    customers: dict[str, Client] = {}
    for i, (name, status, notes) in enumerate(CUSTOMERS):
        customers[name] = Client.create(
            name=name, company=name, status=status, notes=notes,
            email=f"hello@{name.split()[0].lower()}.example",
            created_by=owner, created_at=ts(60 - i), updated_at=ts(60 - i),
        )

    projects: dict[str, Project] = {}
    for name, status, owner_name, customer_name, due_in, quiet, description in PROJECTS:
        projects[name] = Project.create(
            name=name, status=status, description=description,
            owner=_by_name(people, owner_name),
            customer=_by_name(customers, customer_name),
            due_date=d(due_in), last_activity_at=ts(quiet),
            created_by=owner, created_at=ts(quiet + 30), updated_at=ts(quiet),
        )

    for title, customer_name, value, stage, owner_name, action, action_due, quiet in OPPORTUNITIES:
        Opportunity.create(
            title=title, customer=_by_name(customers, customer_name), value=value, stage=stage,
            owner=_by_name(people, owner_name), next_action=action,
            next_action_due=d(action_due) if action_due is not None else None,
            last_activity_at=ts(quiet),
            created_by=owner, created_at=ts(quiet + 25), updated_at=ts(quiet),
        )

    for i, (title, status, priority, due_in, person_name, customer_name, project_name) in enumerate(TASKS):
        person = _by_name(people, person_name)
        Task.create(
            title=title, status=status, priority=priority,
            due_date=d(due_in) if due_in is not None else None,
            owner=person, assignee=person.user if person and person.user_id else None,
            client=_by_name(customers, customer_name),
            project=_by_name(projects, project_name),
            position=i, created_by=owner,
            created_at=ts(min(30, i + 2)), updated_at=ts(min(30, i + 2)),
        )

    for description, person_name, due_in, status, source, customer_name, project_name in COMMITMENTS:
        Commitment.create(
            description=description, person=_by_name(people, person_name),
            due_date=d(due_in), status=status, source=source,
            customer=_by_name(customers, customer_name),
            project=_by_name(projects, project_name),
            created_by=owner, created_at=ts(abs(due_in) + 2), updated_at=ts(abs(due_in)),
        )

    for title, decision, rationale, owner_name, decided_ago, review_in, status, project_name in DECISIONS:
        Decision.create(
            title=title, decision=decision, rationale=rationale,
            owner=_by_name(people, owner_name) or me,
            decided_on=d(-decided_ago),
            review_on=d(review_in) if review_in is not None else None,
            status=status, project=_by_name(projects, project_name),
            created_by=owner, created_at=ts(decided_ago), updated_at=ts(decided_ago),
        )

    notes: list[Note] = []
    for title, days_ago, author_name, tags, body in NOTES:
        notes.append(Note.create(
            title=title, body=body, author=_by_name(people, author_name) or me,
            occurred_on=d(-days_ago), tags=tags, captured=True,
            created_by=owner, created_at=ts(days_ago, 16), updated_at=ts(days_ago, 16),
        ))

    _link_notes(notes, customers, projects)
    _seed_activity(owner, customers, projects)


def _link_notes(notes: list[Note], customers: dict, projects: dict) -> None:
    """A handful of notes wired to the records they're about, so the "where did
    this come from" trail on a customer isn't empty before the first Capture."""
    pairs = [
        (0, "client", "Quanta Semiconductor"),
        (1, "project", "Vertex depot rollout — phase two"),
        (3, "client", "Bluepeak Health"),
        (5, "client", "Northwind Traders"),
        (6, "project", "SSO and SCIM"),
        (8, "project", "Riverstone migration"),
        (10, "client", "Acme Corp"),
        (11, "client", "Harbourline Freight"),
        (12, "client", "Palisade Legal"),
        (14, "client", "Kestrel Analytics"),
    ]
    for index, subject_type, name in pairs:
        source = customers if subject_type == "client" else projects
        target = source.get(name)
        if target is not None and index < len(notes):
            NoteLink.create(note=notes[index], subject_type=subject_type, subject_id=target.id)


def _seed_activity(owner: User, customers: dict, projects: dict) -> None:
    """Backdated Activity rows so "What's changed" has something to say on the
    very first page load. Core writes these on every mutation; a seeded
    instance has no mutation history, so it gets a plausible one."""
    quanta = customers.get("Quanta Semiconductor")
    vertex = projects.get("Vertex depot rollout — phase two")
    sso = projects.get("SSO and SCIM")
    harbourline = customers.get("Harbourline Freight")

    entries = [
        (quanta, "client", "status_changed", {"old": "lead", "new": "active"}, 1),
        (vertex, "project", "status_changed", {"old": "active", "new": "at_risk"}, 2),
        (sso, "project", "status_changed", {"old": "active", "new": "blocked"}, 3),
        (harbourline, "client", "updated", {}, 4),
        (quanta, "client", "commented", {}, 5),
        (vertex, "project", "updated", {}, 6),
    ]
    for target, subject_type, verb, payload, days_ago in entries:
        if target is None:
            continue
        Activity.create(
            subject_type=subject_type, subject_id=target.id, actor=owner, verb=verb,
            payload_json=json.dumps(payload), created_at=ts(days_ago, 11),
        )


if __name__ == "__main__":  # pragma: no cover - developer convenience
    import sys

    sys.path.insert(0, "vendor")
    from models import ensure_schema

    ensure_schema()
    first = User.select().order_by(User.id).first()
    if first is None:
        raise SystemExit("Register an owner first, then run this to seed.")
    seed_demo_data(first)
    print(f"Seeded a demo company for {first.email}.")
