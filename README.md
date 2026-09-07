# Binders Pro

A small startup operating system: the customers you're selling to, the work
you owe them, the promises people made, and the decisions you took — with a
box on the dashboard that turns a pasted meeting note into all four.

Built on [Binders Core](../core): same Bottle + peewee + SQLite architecture,
same zero-dependency constraint, same conventions. Core is Clients and Tasks;
Pro adds People, Projects, Opportunities, Commitments, Decisions and Notes,
and two screens that read the whole company back to you.

- **What's built:** [DOCUMENTATION.md](DOCUMENTATION.md)
- **Working on this app?** Read [AGENTS.md](AGENTS.md) first.
- **What it's meant to be:** [SPEC.md](SPEC.md)

## The three things it does

**Dashboard (`/`)** — the CEO's thirty seconds. What needs attention, five
headline numbers, what changed this week, what was decided. Every line is
computed from the data, not generated.

**Cracks (`/cracks`)** — the COO's version of the same question. Everything
overdue, at risk, gone quiet, or owned by nobody, sorted by how long it's been
that way. Plus a table of who promised what this week.

**Capture** — the box at the top of the dashboard. Paste a meeting note, an
email, or a brain dump; Binders proposes the records worth keeping, you tick
the ones you want, and they appear across the customer, the pipeline and the
commitments — each linked back to the text it came from.

## Requirements

- Python 3.10+ (the code uses `X | None` type syntax).
- Nothing else to run the app. `bottle` and `peewee` are vendored as plain
  `.py` files under `vendor/`; the only pip line is gunicorn, and that's
  dev-only.
- **Capture and Ask AI** need a model: set `OPENAI_API_KEY`, or run Ollama
  locally. Without one, every other page works normally and Capture says so
  plainly instead of failing.

## Quick start

```bash
cp .env.example .env   # optional — sensible defaults work without it
python3 app.py
```

Then open http://localhost:8000 and register. The first account becomes the
owner, and the instance seeds itself with a fictional company — eight people,
fifteen customers, ten deals, six projects, forty tasks, fifteen commitments,
ten decisions and twenty notes — so the dashboard has something to say from
the first page load. Some of it is deliberately going wrong; that's the point.

To reseed a scratch database:

```bash
SQLITE_PATH=/tmp/scratch.db python3 seed.py
```

## Layout

```
app.py         wiring: config, hooks, error pages, template globals
models.py      every table, and ensure_schema() (the whole migration story)
pages.py       Core's routes: auth, customers, tasks, comments, chat
insights.py    the derived state both dashboards read — overdue, at risk, stalled
ai.py          explain (chat tools) + extract (JSON mode)
seed.py        the demo company
modules/
  dashboard/   / and /cracks, and the ledger partial they share
  crm/         opportunities, people
  ops/         projects, commitments
  capture/     the Capture routes, notes, decisions
```

See [AGENTS.md](AGENTS.md) for what belongs where and why.
