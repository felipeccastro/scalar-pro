"""Pro's feature modules.

A module is a plain folder with a `pages.py` and a `templates/` directory —
no plugin system, no registry class, no entry points. `register()` below adds
each module's templates to bottle's lookup path and imports its `pages`, which
registers routes the same way top-level `pages.py` does: `@app.route(...)`
decorators evaluated at import time.

What a module does NOT own is models. Every table lives in the one top-level
`models.py`, because the whole value of this app is that a Commitment can
point at a Customer which can point at a Project — splitting peewee models
across packages would buy tidier folders in exchange for import cycles and a
relational backbone full of holes. Modules are a way to group *pages*.

Templates are namespaced by a subdirectory matching the module name
(`modules/crm/templates/crm/opportunities_list.html`, rendered as
`render("crm/opportunities_list.html")`). bottle's TEMPLATE_PATH is a flat
list searched in order, so without that prefix two modules could not both
have a `list.html`, and which one won would depend on registration order.

Adding a module: create the folder, add its name to MODULES, done.
"""

from __future__ import annotations

import importlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Order matters only for template lookup precedence, which the per-module
# subdirectory prefix already makes moot. Listed in the order a person meets
# them: the two dashboards, then the records they summarise.
MODULES = ("dashboard", "crm", "ops", "capture")


def register(template_path: list[str]) -> None:
    """Wire every module into the running app. Called from app.py right after
    `import pages`, so module routes are registered after the core ones and
    `app` / `render` already exist for them to import."""
    for name in MODULES:
        templates = os.path.join(HERE, name, "templates")
        if os.path.isdir(templates):
            template_path.insert(0, templates)
        importlib.import_module(f"modules.{name}.pages")
