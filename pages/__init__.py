"""Pro's page files.

Flat modules of `@app.route` view functions, one per screen group: core.py
holds Core's routes (auth, customers, tasks, comments, attachments, chat)
plus the shared helpers the other four import from it; dashboard.py, crm.py,
ops.py and capture.py are Pro's own — what used to be separate
modules/<name>/pages.py packages, before everything under pages/ moved into
one flat file per group (see AGENTS.md).

app.py imports `pages.core` directly (it must go first: the other four
import shared helpers from it) and then calls `register()` below, which
imports the remaining four for their route-registering side effects.

What none of these files own is models — every table lives in models.py,
same as before this reorg.

Templates for all five live together under the top-level templates/
directory, namespaced by a subdirectory matching each file's name
(templates/crm/opportunities_list.html, rendered as
render("crm/opportunities_list.html")); core.py's own templates are the ones
with no such prefix. Nothing here has to register a second TEMPLATE_PATH
entry for that — app.py already points bottle at templates/ once, and that
one directory now holds everything.
"""

from __future__ import annotations

import importlib

# Order matters only for template lookup precedence, which the per-file
# subdirectory prefix already makes moot. Listed in the order a person meets
# them: the two dashboards, then the records they summarize. core.py isn't
# here — app.py imports it directly, before calling register().
PAGES = ("dashboard", "crm", "ops", "capture")


def register() -> None:
    for name in PAGES:
        importlib.import_module(f"pages.{name}")
