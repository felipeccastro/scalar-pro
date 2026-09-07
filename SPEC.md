# Binders Pro — Startup OS Demo Spec

# Product goal

Build a Pro startup operating system around three core experiences:

1. **CEO:** “Tell me what I need to know.”
2. **COO:** “Show me what’s falling through the cracks.”
3. **Everyone:** “Turn messy business text into structured state.”

The system should feel like a coherent business application, not a collection of modules.

# Core data model

Keep the underlying model small.

# People

- name
- role
- email
- active/inactive

# Companies / Customers

- name
- status
- owner
- value
- notes

# Projects

- name
- status
- owner
- due date
- description

# Tasks

- title
- status
- owner
- due date
- priority
- related project/customer
- description

# Commitments

A commitment is essentially a task with stronger semantics.

- description
- person responsible
- due date
- status
- source
- related customer/project

Statuses:

- Open
- Done
- Overdue
- Cancelled

# Opportunities

- customer
- value
- stage
- owner
- next action
- next action due
- notes

Stages:

- Lead
- Qualified
- Proposal
- Negotiation
- Won
- Lost

# Decisions

- title
- decision
- rationale
- owner
- date
- review date
- status
- related project/customer

# Notes / Entries

The raw text layer.

- title
- body
- author
- date
- tags
- extracted entities

This is important: **don't make structured records replace text.** The text remains the source/context from which structure can emerge.

# 1. CEO Cockpit

The default Pro home screen.

Headline:

> **Good morning. Here's what needs your attention.**

Then four sections.

# Attention

Things requiring CEO action.

Examples:

- 3 overdue commitments
- Acme proposal due today
- Project X is 6 days behind
- Deal Y has been stalled for 14 days

Each item is clickable.

# Business snapshot

Small number of useful metrics:

- Pipeline
- Open opportunities
- Active projects
- Overdue commitments
- Tasks due this week

Don't turn this into a BI dashboard.

# What's changed

Show meaningful changes since the previous period:

> Acme moved to Negotiation  
> Project Alpha became At Risk  
> 4 commitments completed  
> New $50k opportunity created

# Decisions

Recent decisions + decisions awaiting review.

The CEO should be able to understand the company in **30 seconds**.

# 2. COO Control Center

Separate view optimized for operational problems.

Headline:

> **What's falling through the cracks?**

# Overdue

List:

- overdue tasks
- overdue commitments
- overdue opportunities/actions
- overdue projects

Sort by age.

# At risk

Projects where:

- deadline approaching
- incomplete work
- overdue tasks
- blocked status

Show:

> **Website redesign**  
> Due Friday · 7 tasks remaining · 2 overdue

# Stalled

Detect things that haven't moved.

Examples:

> Enterprise deal — no activity for 12 days  
> Hiring — no update for 9 days

# Unassigned

Things without an owner.

This is a surprisingly good COO feature.

> 7 tasks have no owner  
> 2 opportunities have no owner

# This week's commitments

A clean operational list:

| Person | Commitment | Due | Status |
|---|---|---|---|

The COO should be able to answer:

> **Who promised what, and are they doing it?**

# 3. Text → Business State

This is the Binders-specific magic.

Create a prominent action:

> **Capture**

User pastes arbitrary business text.

Example:

> Met with Acme today. They want the enterprise plan at around $50k/year. They need SSO before signing. João will investigate the integration and I'll send a proposal Friday. They're also talking to Competitor X.

Binder analyzes it and proposes:

# Customer

**Acme**

# Opportunity

**Enterprise — $50,000**

Stage → Negotiation

# Requirement

**SSO integration**

# Commitment

**João — investigate SSO**

# Commitment

**Felipe — send proposal**

Due → Friday

# Competitor

**Competitor X**

User sees:

> **Create 6 records**

and confirms.

The records retain a link back to the original note.

That's the important part.

# Capture should also work for

# Meeting notes

Paste meeting transcript/notes → extract:

- people
- companies
- decisions
- tasks
- commitments
- opportunities

# Email-like text

Paste an email → identify:

- customer
- request
- deadline
- owner
- next action

# Random brain dump

Even something like:

> Need to talk to Maria about hiring. Also Acme wants SSO. We should probably reconsider pricing before next month's launch.

Should produce proposed:

- commitment
- customer requirement
- decision/topic

The user approves what gets created.

# 4. Global search

Search everything.

A single search box:

> Search Binders...

Search:

- people
- customers
- projects
- tasks
- opportunities
- decisions
- commitments
- notes

Results should show **relationships**, not just matching records.

Searching `Acme` should reveal:

> Acme  
> Opportunity: $50k Enterprise  
> Project: SSO  
> 3 commitments  
> 2 decisions  
> 7 notes

This makes the system feel much more intelligent than a collection of CRUD screens.

# 5. Company timeline

Every important business event goes into a timeline.

Example:

**Sep 7**

> Acme moved to Negotiation  
> Felipe created $50k opportunity

**Sep 6**

> João committed to investigate SSO

**Sep 4**

> Decision: prioritize enterprise launch

**Sep 2**

> Acme meeting captured

This gives the company a memory.

# 6. Decision log

Simple but polished.

Create decision:

> **Title:** Launch enterprise plan in October

Fields:

- Decision
- Why
- Owner
- Date
- Review date
- Related records

Decision page should show:

> **Why did we make this decision?**

and:

> **What happened afterward?**

This becomes particularly powerful when combined with the timeline.

# 7. Relationships

Don't over-engineer this.

Records should be linkable:

    Customer
     ├── Opportunities
     ├── Projects
     ├── Tasks
     ├── Commitments
     ├── Decisions
     └── Notes

And:

    Project
     ├── Tasks
     ├── Commitments
     ├── Decisions
     └── Notes

This relational backbone is what makes the dashboards possible.

# 8. AI

For this demo, AI should do **three things only**.

# Extract

Text → structured records.

# Summarize

Records → CEO/COO summaries.

# Explain

Ask questions such as:

> What needs my attention?

> What's stalled?

> What did we decide about pricing?

> What did João promise?

The AI should **query the actual application state**, not hallucinate an answer from the conversation.

# 9. Demo seed data

This is critical.

Don't demo an empty system.

Preload a fictional startup with:

- ~8 people
- ~15 customers
- ~10 opportunities
- ~6 projects
- ~40 tasks
- ~15 commitments
- ~10 decisions
- ~20 notes

Intentionally include problems:

- overdue commitments
- stalled opportunities
- unassigned tasks
- at-risk projects
- recent decisions
- messy meeting notes

The dashboard should therefore immediately have something interesting to say.

# 10. The actual demo

I'd structure the demo around **one story**, not feature tours.

# Scene 1 — CEO

Open Binders.

> “I haven't looked at the company yet today.”

Dashboard immediately says:

> **3 things need your attention.**

Click through them.

Then:

> “What's changed this week?”

Show the changes.

# Scene 2 — COO

Switch to:

> **What's falling through the cracks?**

Show:

- overdue
- stalled
- unassigned
- at-risk

Then:

> “Who promised what?”

Show commitments.

# Scene 3 — Magic

Open Capture.

Paste the messy Acme meeting.

Binders extracts the business state.

Confirm.

Go back to Acme.

Suddenly the information appears throughout the system:

**Opportunity → commitment → customer → timeline → dashboard.**

That's the **wow**.

And importantly, this gives you a very clean architecture for the Pro app:

**Text is the input. Structured state is the system. CEO/COO views are the output.**

Everything else can wait.