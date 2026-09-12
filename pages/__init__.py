"""Every route in the app, split by feature area. Still no blueprints — each
module below does `from app import app` and decorates its own routes
directly, exactly as one flat pages/core.py (Core's routes) and four more
flat modules (Pro's own) used to; this package just gives each feature area
its own file, the same move Core's own pages.py went through first (see
AGENTS.md) — now applied uniformly instead of leaving Pro's biggest file out
of it.

Nine of these are Core's: auth, clients, tasks, comments, attachments,
notifications, settings, chat, and the ⌘K quick-search palette. Four are
Pro's own — dashboard, crm, ops, capture — plus `_shared.py`, which is not a
route module: it's the handful of query/redirect helpers used by more than
one of the files below (`_load_comments`, `_active_clients`, `_people`,
`_open_projects`, …).

What none of these files own is models — every table lives in models.py.
Templates for all thirteen live together under the top-level templates/
directory, namespaced by a subdirectory matching each Pro-specific file's
name (templates/crm/opportunities_list.html, rendered as
render("crm/opportunities_list.html")); the nine Core-derived files' own
templates are the ones with no such prefix.
"""

from __future__ import annotations

from bottle import HTTPError, request

from app import app
from utils import (
    PUBLIC_ROUTES,
    SESSION_INDEPENDENT_PATHS,
    any_team_members_exist,
    current_user,
    redirect,
    url_for,
)


@app.hook("before_request")
def _bootstrap_redirect() -> None:
    """Until the first owner account exists, every road leads to /register."""
    if request.path == "/register" or request.path.startswith("/static/"):
        return
    if not any_team_members_exist():
        redirect(url_for("register_owner"))


@app.hook("before_request")
def _require_login_hook() -> None:
    """Every route requires a logged-in user by default — the opposite of a
    per-route @require_login decorator, which is easy to forget on a new
    route and silently leave unprotected. PUBLIC_ROUTES (utils.py) lists
    the handful of routes a signed-out visitor genuinely needs to reach
    (register, login, accept-invite, forgot/reset password); everything
    else redirects to /login.

    Registered *after* _bootstrap_redirect above — before_request hooks run
    in registration order (see Bottle's add_hook) — so a fresh, team-less
    instance always lands on /register first, before this hook gets a
    chance to bounce it to /login instead.

    Resolves the route itself via app.match() rather than checking
    request.route: before_request hooks fire *before* Bottle's own routing
    (see utils.py's SESSION_INDEPENDENT_PATHS comment), so there's no route
    to inspect yet at this point otherwise. match() is a plain, read-only
    lookup (see Router.match) — cheap to do twice per request.
    """
    if request.path.startswith("/static/") or request.path in SESSION_INDEPENDENT_PATHS:
        return
    try:
        route, _ = app.match(request.environ)
    except HTTPError:
        return  # a 404/405 — let Bottle's own routing surface that normally
    if route.name in PUBLIC_ROUTES:
        return
    if current_user() is None:
        redirect(url_for("login"))


# Import each feature module for its route-registration side effects only.
# Listed alphabetically — order doesn't matter for registration itself (every
# module resolves other routes lazily, by name, via url_for at request time,
# not at import time), and modules needing a shared helper (crm.py, ops.py,
# capture.py) import it straight from _shared rather than from whichever
# route module happened to load first.
from . import (  # noqa: E402,F401
    attachments,
    auth,
    capture,
    chat,
    clients,
    comments,
    crm,
    dashboard,
    notifications,
    ops,
    palette,
    settings,
    tasks,
)
