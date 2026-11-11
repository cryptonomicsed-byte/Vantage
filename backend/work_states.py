"""One state vocabulary for every kind of work item (G3).

Four task systems grew four vocabularies: the trading space says
``proposed / claimed / review / accepted``, the content space says
``brief_submitted / in_production / in_review / published``, the lounge
says ``pending / in_progress / completed`` and the job loop says
``open / claimed / submitted / approved``. A board or a control center
that wants to count "work waiting for review" had to know all four.

This module is the shared vocabulary and the mapping onto it. Each system
keeps writing its own words (changing the stored values is a migration
with callers in the UI); every list route adds ``status_canonical`` so a
reader needs only one.
"""
from __future__ import annotations

PROPOSED = "proposed"      # created, nobody holds it
CLAIMED = "claimed"        # someone holds a lease on it
REVIEW = "review"          # an artifact was delivered, awaiting a reviewer
ACCEPTED = "accepted"      # the reviewer accepted the artifact
REJECTED = "rejected"      # sent back
CLOSED = "closed"          # finished, receipt recorded
PUBLISHED = "published"    # content only: accepted and released

STATES = (PROPOSED, CLAIMED, REVIEW, ACCEPTED, REJECTED, CLOSED, PUBLISHED)

#: Which raw statuses each kind uses for which canonical state. A raw
#: status not listed maps onto itself when it is already canonical, and
#: onto "proposed" otherwise -- unknown words must not become invisible.
_MAPS: dict[str, dict[str, str]] = {
    "vtask": {  # vantage_tasks (trading and content spaces, workspaces)
        "proposed": PROPOSED, "claimed": CLAIMED, "executing": CLAIMED, "blocked": CLAIMED,
        "review": REVIEW, "accepted": ACCEPTED, "rejected": REJECTED, "closed": CLOSED,
        # content space words
        "brief_submitted": PROPOSED, "in_production": CLAIMED, "in_review": REVIEW,
        "published": PUBLISHED,
    },
    "gtask": {  # guild_tasks
        "proposed": PROPOSED, "claimed": CLAIMED, "executing": CLAIMED, "blocked": CLAIMED,
        "review": REVIEW, "accepted": ACCEPTED, "rejected": REJECTED, "closed": CLOSED,
    },
    "ltask": {  # lounge_tasks
        "pending": PROPOSED, "in_progress": CLAIMED, "review": REVIEW,
        "completed": CLOSED, "done": CLOSED, "cancelled": REJECTED, "failed": REJECTED,
    },
    "jobtask": {  # job_tasks
        "open": PROPOSED, "claimed": CLAIMED, "submitted": REVIEW,
        "approved": ACCEPTED, "rejected": REJECTED, "completed": CLOSED,
    },
    "task": {  # task_listings (USDC marketplace)
        "open": PROPOSED, "claimed": CLAIMED, "awarded": CLAIMED, "submitted": REVIEW,
        "completed": CLOSED, "cancelled": REJECTED,
    },
    "tro": {
        "open": PROPOSED, "matched": CLAIMED, "completed": CLOSED, "cancelled": REJECTED,
    },
}


def canonical_status(kind: str, raw: str | None) -> str:
    raw = (raw or "").strip().lower()
    mapped = _MAPS.get(kind, {}).get(raw)
    if mapped:
        return mapped
    return raw if raw in STATES else PROPOSED


def with_canonical(kind: str, rows: list[dict]) -> list[dict]:
    """Add ``status_canonical`` to each row in place and return the list."""
    for r in rows:
        r["status_canonical"] = canonical_status(kind, r.get("status"))
    return rows


def envelope(kind: str, rows: list[dict], *, total: int | None = None, legacy_key: str | None = None) -> dict:
    """The shared list envelope ``{items, total}``.

    ``legacy_key`` keeps the key an existing UI reads (``tasks``, ``pools``)
    pointing at the same list, so the board and the control center can
    read one shape without a flag day in the frontend.
    """
    rows = with_canonical(kind, rows)
    out = {"items": rows, "total": len(rows) if total is None else total}
    if legacy_key:
        out[legacy_key] = rows
    return out
