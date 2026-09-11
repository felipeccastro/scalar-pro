"""Quick search across every record type, via SQLite FTS5.

Same technique as admin's services/search.py: `search_index` holds one row
per record — title, body, and anything commented on it — plus kind/entity_id
so a hit resolves back to a real row (via insights.SUBJECT_REGISTRY, the same
table the activity feed and Capture's "what came out of this note" list
already resolve through). The index is maintained explicitly on writes via
index_entity()/delete_entity()/reindex_subject(), called from pages/*.py and
ai.py right next to each record_activity() call — never through DB triggers,
so a call site stays as simple to read as the write it's indexing.

The FTS5 table itself is created in models.ensure_schema(); this module only
ever queries and writes rows in it.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass

from models import Comment, db
import insights

# The eight kinds worth finding by quick search — exactly the keys of
# insights.SUBJECT_REGISTRY, reused rather than duplicated so a new
# commentable record type only has to be taught to that one table.
SEARCHABLE_KINDS = tuple(insights.SUBJECT_REGISTRY.keys())


def entity_kind(entity) -> str:
    return type(entity).__name__.lower()


def _entity_text(kind: str, entity) -> tuple[str, str]:
    """(title, body) for one record. `body` is whatever else on the row is
    worth matching — the fields a person would actually type a few words
    from, not every column."""
    if kind == "client":
        title = entity.name or ""
        body = "\n".join(filter(None, [entity.email, entity.phone, entity.company, entity.notes]))
    elif kind == "task":
        title = entity.title or ""
        body = entity.description or ""
    elif kind == "person":
        title = entity.name or ""
        body = "\n".join(filter(None, [entity.role, entity.email]))
    elif kind == "project":
        title = entity.name or ""
        body = entity.description or ""
    elif kind == "opportunity":
        title = entity.title or ""
        body = "\n".join(filter(None, [entity.next_action, entity.notes]))
    elif kind == "commitment":
        # No separate title field — the description *is* the record.
        title = entity.description or ""
        body = ""
    elif kind == "decision":
        title = entity.title or ""
        body = "\n".join(filter(None, [entity.decision, entity.rationale]))
    elif kind == "note":
        title = entity.title or ""
        body = "\n".join(filter(None, [entity.body, entity.tags]))
    else:
        raise ValueError(f"not a searchable kind: {kind!r}")
    return title, body


def _comments_blob(kind: str, entity_id: int) -> str:
    bodies = [
        c.body
        for c in Comment.select(Comment.body).where(
            (Comment.subject_type == kind) & (Comment.subject_id == entity_id)
        )
    ]
    return "\n".join(bodies)


# ---------------------------------------------------------------------------
# Index maintenance
# ---------------------------------------------------------------------------


def index_entity(entity) -> None:
    """Insert or replace the search row for one record."""
    kind = entity_kind(entity)
    title, body = _entity_text(kind, entity)
    comments = _comments_blob(kind, entity.id)

    delete_entity(kind, entity.id)
    db.execute_sql(
        """
        INSERT INTO search_index (title, body, comments, kind, entity_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, body, comments, kind, entity.id),
    )


def delete_entity(kind: str, entity_id: int) -> None:
    db.execute_sql("DELETE FROM search_index WHERE kind = ? AND entity_id = ?", (kind, entity_id))


def reindex_subject(subject_type: str, subject_id: int) -> None:
    """Re-derive the search row for whatever a comment was just left on or
    removed from. Comment bodies are folded into their subject's row (see
    _comments_blob) rather than indexed on their own, so a comment write
    invalidates the subject's row, not a row of its own."""
    spec = insights.SUBJECT_REGISTRY.get(subject_type)
    if spec is None:
        return
    entity = spec["model"].get_or_none(spec["model"].id == subject_id)
    if entity is None:
        delete_entity(subject_type, subject_id)
        return
    index_entity(entity)


def backfill_if_empty() -> None:
    """Populate search_index from every existing row, but only if it's
    currently empty. Covers two moments the explicit index_entity() call
    sites never see: an existing instance upgrading to this feature (rows
    already exist; ensure_schema() only just created the empty table), and a
    freshly seeded demo (seed_demo_data() calls this again once it's done —
    see seed.py). Ongoing writes after either point go through index_entity()
    at their own call sites, same as admin."""
    (count,) = db.execute_sql("SELECT count(*) FROM search_index").fetchone()
    if count:
        return
    for kind in SEARCHABLE_KINDS:
        model = insights.SUBJECT_REGISTRY[kind]["model"]
        for row in model.select():
            index_entity(row)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@dataclass
class SearchHit:
    kind: str
    entity_id: int
    title: str
    snippet: str
    rank: float


# Split on anything that isn't a unicode word char — matches how FTS5's
# unicode61 tokenizer breaks tokens, so what we search on lines up with what
# got indexed.
_TOKEN_RE = _re.compile(r"\w+", _re.UNICODE)


def _tokens(raw: str) -> list[str]:
    return _TOKEN_RE.findall(raw or "")


def _sqlite_match(tokens: list[str]) -> str:
    # Prefix-match every token so "phas" finds "phase 2" while still typing.
    return " ".join(f'"{tok}"*' for tok in tokens)


def search(q: str, *, kind: str | None = None, limit: int = 20) -> list[SearchHit]:
    tokens = _tokens(q)
    if not tokens:
        return []

    sql = """
        SELECT
            kind, entity_id, title,
            snippet(search_index, 1, '<mark>', '</mark>', '…', 12) AS snip,
            bm25(search_index) AS rk
        FROM search_index
        WHERE search_index MATCH ?
    """
    params: list = [_sqlite_match(tokens)]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    # bm25 is lower-is-better, so ascending order is best-first.
    sql += " ORDER BY rk LIMIT ?"
    params.append(limit)

    cur = db.execute_sql(sql, params)
    return [
        SearchHit(kind=row[0], entity_id=row[1], title=row[2] or "", snippet=row[3] or "", rank=float(row[4]))
        for row in cur.fetchall()
    ]
