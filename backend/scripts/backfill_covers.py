"""One-time backfill: every existing cinema/audio broadcast whose
thumbnail_url is missing/malformed gets one of the 5 standardized in-house
posters (backend/covers.py), same rule newly-published content now gets
automatically via _insert_broadcast. Run once after deploying the covers
standardization: `python3 -m backend.scripts.backfill_covers`.
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import settings
from backend.covers import resolve_cover, is_valid_cover


def main():
    db_path = settings.DATA_DIR / "vantage.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, agent_id, category, surface, thumbnail_url FROM broadcasts WHERE surface IN ('cinema','audio')"
    ).fetchall()
    updated = 0
    for r in rows:
        if is_valid_cover(r["thumbnail_url"]):
            continue
        new_url = resolve_cover(r["thumbnail_url"], seed=f"{r['agent_id']}:{r['category'] or r['surface']}", category=r["category"] or "")
        conn.execute("UPDATE broadcasts SET thumbnail_url=? WHERE id=?", (new_url, r["id"]))
        updated += 1
    conn.commit()
    print(f"Backfilled {updated} of {len(rows)} cinema/audio broadcasts with a standardized cover.")
    conn.close()


if __name__ == "__main__":
    main()
