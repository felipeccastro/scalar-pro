"""Quick search — the ⌘K command palette (layout.html). See search.py."""

from __future__ import annotations

from bottle import request

from app import app, render
import insights
import search


@app.route("/search/palette", method="GET", name="search_palette")
def search_palette():
    q = (request.query.get("q") or "").strip()
    hits = search.search(q, limit=10) if q else []
    results = []
    for hit in hits:
        results.append({
            "kind": hit.kind,
            "label": insights.subject_label(hit.kind),
            "badge": insights.subject_badge(hit.kind),
            "title": hit.title,
            "url": insights.subject_url(hit.kind, hit.entity_id),
        })
    return render("_palette_results.html", q=q, results=results)
