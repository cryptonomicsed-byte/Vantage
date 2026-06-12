"""Analytics and Leaderboard endpoints."""
import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from ..db import DB_PATH
from ..deps import get_agent
from ..config import settings

router = APIRouter(prefix="/api/agents", tags=["analytics"])

@router.get("/me/analytics")
async def agent_analytics(agent: dict = Depends(get_agent)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Views by day (last 30 days)
        async with db.execute(
            """SELECT date(ve.viewed_at) as day, COUNT(*) as views
               FROM view_events ve
               JOIN broadcasts b ON b.id = ve.broadcast_id
               WHERE b.agent_id=? AND ve.viewed_at >= datetime('now', '-30 days')
               GROUP BY day ORDER BY day""",
            (agent["id"],),
        ) as cur:
            vbd = await cur.fetchall()

        # Top 5 broadcasts
        async with db.execute(
            """SELECT id, title, thumbnail_url, view_count, content_type
               FROM broadcasts WHERE agent_id=? AND status='ready'
               ORDER BY view_count DESC LIMIT 5""",
            (agent["id"],),
        ) as cur:
            top = await cur.fetchall()

        # Totals
        async with db.execute(
            """SELECT SUM(view_count) as total_views,
                      COUNT(*) as total_broadcasts
               FROM broadcasts WHERE agent_id=? AND status='ready'""",
            (agent["id"],),
        ) as cur:
            totals = await cur.fetchone()

        # Content type breakdown
        async with db.execute(
            """SELECT content_type, COUNT(*) as cnt
               FROM broadcasts WHERE agent_id=? AND status='ready'
               GROUP BY content_type""",
            (agent["id"],),
        ) as cur:
            breakdown = await cur.fetchall()

        # Reactions by day (last 30 days)
        async with db.execute(
            """SELECT date(r.created_at) as day, COUNT(*) as count
               FROM reactions r
               JOIN broadcasts b ON b.id = r.broadcast_id
               WHERE b.agent_id=? AND r.created_at >= datetime('now', '-30 days')
               GROUP BY day ORDER BY day""",
            (agent["id"],),
        ) as cur:
            rbd = await cur.fetchall()

        # Comments by day (last 30 days)
        async with db.execute(
            """SELECT date(c.created_at) as day, COUNT(*) as count
               FROM comments c
               JOIN broadcasts b ON b.id = c.broadcast_id
               WHERE b.agent_id=? AND c.created_at >= datetime('now', '-30 days')
               GROUP BY day ORDER BY day""",
            (agent["id"],),
        ) as cur:
            cbd = await cur.fetchall()

        # Follower count
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM agent_follows WHERE following_id=?", (agent["id"],)
        ) as cur:
            fc = await cur.fetchone()

        # Top reacted broadcasts
        async with db.execute(
            """SELECT b.id, b.title, b.thumbnail_url, b.content_type,
                      COUNT(r.broadcast_id) as reaction_count
               FROM broadcasts b LEFT JOIN reactions r ON r.broadcast_id = b.id
               WHERE b.agent_id=? AND b.status='ready'
               GROUP BY b.id ORDER BY reaction_count DESC LIMIT 5""",
            (agent["id"],),
        ) as cur:
            top_reacted = await cur.fetchall()

        # Average watch time
        async with db.execute(
            """SELECT AVG(ve.watch_seconds) as avg_watch,
                      SUM(ve.watch_seconds) / 3600.0 as total_hours
               FROM view_events ve
               JOIN broadcasts b ON b.id = ve.broadcast_id
               WHERE b.agent_id=? AND ve.watch_seconds > 0""",
            (agent["id"],),
        ) as cur:
            wt = await cur.fetchone()

    return {
        "views_by_day": [dict(r) for r in vbd],
        "top_broadcasts": [dict(r) for r in top],
        "total_views": totals["total_views"] or 0 if totals else 0,
        "total_broadcasts": totals["total_broadcasts"] or 0 if totals else 0,
        "content_type_breakdown": {r["content_type"]: r["cnt"] for r in breakdown},
        "reactions_by_day": [dict(r) for r in rbd],
        "comments_by_day": [dict(r) for r in cbd],
        "follower_count": fc["cnt"] if fc else 0,
        "top_reacted": [dict(r) for r in top_reacted],
        "avg_watch_seconds": wt["avg_watch"] or 0.0 if wt else 0.0,
        "total_watch_hours": wt["total_hours"] or 0.0 if wt else 0.0,
    }

@router.get("/leaderboard")
async def get_leaderboard(limit: int = 20):
    """Agent leaderboard ranked by token balance (SUI-enabled) or view count."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if settings.SUI_ENABLED:
            async with db.execute(
                """SELECT a.name, a.avatar_url, a.bio, a.sui_address, a.token_balance,
                          COUNT(b.id) as broadcast_count, SUM(COALESCE(b.view_count,0)) as total_views
                   FROM agents a LEFT JOIN broadcasts b ON b.agent_id=a.id AND b.status='ready'
                   GROUP BY a.id ORDER BY a.token_balance DESC, total_views DESC LIMIT ?""",
                (limit,),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                """SELECT a.name, a.avatar_url, a.bio, a.sui_address, COALESCE(a.token_balance,0) as token_balance,
                          COUNT(b.id) as broadcast_count, SUM(COALESCE(b.view_count,0)) as total_views
                   FROM agents a LEFT JOIN broadcasts b ON b.agent_id=a.id AND b.status='ready'
                   GROUP BY a.id ORDER BY total_views DESC LIMIT ?""",
                (limit,),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    return rows
