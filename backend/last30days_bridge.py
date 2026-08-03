"""Real integration with github.com/mvanhorn/last30days-skill (vendored
as a real git submodule at vendor/last30days-skill, pinned to a commit
like any other dependency): a multi-source social/market research tool
(Reddit, Hacker News, Polymarket, GitHub, arXiv, etc.) that scores by
real engagement rather than editorial ranking.

Confirmed live, not assumed: `last30days.py <topic> --emit=json
--json-profile=raw --quick --no-browser-cookies` is explicitly designed
as "an unattended cron host" (comment in the upstream watchlist.py) --
zero runtime pip dependencies (pure stdlib), and the "raw" profile skips
the interactive AI-judge synthesis step entirely, so this genuinely runs
headless with zero API keys configured (verified: a real run against
"solana" returned live Reddit + Hacker News findings with no
SCRAPECREATORS_API_KEY/OPENAI_API_KEY/etc. set at all).

This module bypasses upstream's own watchlist.py/store.py (which keeps
its own SQLite dedup state) in favor of Vantage's own watch-topics table
and its own dedup tracking -- simpler to reason about and keeps
everything in Vantage's one database rather than a second SQLite file
whose location has to survive daemon restarts too.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "vendor" / "last30days-skill" / "skills" / "last30days" / "scripts" / "last30days.py"
RUN_TIMEOUT_SECONDS = 300


async def run_topic_raw(topic: str, lookback_days: int = 30) -> Optional[dict]:
    """Runs one topic through last30days.py in raw/quick/unattended mode
    and returns the parsed JSON report, or None on any failure. Never
    raises -- a broken/slow research run must never take down the
    background loop that calls this repeatedly."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", str(SCRIPT_PATH), topic,
            "--emit=json", "--json-profile=raw", "--quick",
            "--lookback-days", str(lookback_days),
            "--no-browser-cookies",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=RUN_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            logger.warning("last30days run failed for topic %r: %s", topic, stderr.decode(errors="replace")[-500:])
            return None
        return json.loads(stdout)
    except asyncio.TimeoutError:
        logger.warning("last30days run timed out for topic %r", topic)
        return None
    except Exception as e:
        logger.warning("last30days run errored for topic %r: %s", topic, e)
        return None


def extract_findings(report: dict) -> list[dict]:
    """Flattens the raw report's nested cluster/item structure into a
    flat list of {item_id, title, url, source, snippet, published_at,
    score}. Tolerant of the exact nesting shape shifting between
    versions -- walks whatever list-of-dicts it finds under common keys
    rather than asserting one rigid schema."""
    findings = []

    def walk(node):
        if isinstance(node, dict):
            if "item_id" in node and ("title" in node or "url" in node):
                findings.append({
                    "item_id": node.get("item_id"),
                    "title": node.get("title", ""),
                    "url": node.get("url", ""),
                    "source": node.get("source", ""),
                    "snippet": node.get("snippet", ""),
                    "published_at": node.get("published_at", ""),
                    "score": node.get("local_rank_score", node.get("relevance_hint", 0)),
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(report)
    return findings


async def _already_seen(topic: str, item_id: str) -> bool:
    if not item_id:
        return False
    async with get_db() as db:
        cur = await db.execute(
            "SELECT 1 FROM last30days_seen WHERE topic=? AND item_id=?", (topic, item_id)
        )
        return (await cur.fetchone()) is not None


async def _mark_seen(topic: str, item_id: str) -> None:
    if not item_id:
        return
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO last30days_seen (topic, item_id, seen_at) VALUES (?,?,?)",
            (topic, item_id, int(time.time())),
        )
        await db.commit()


async def run_watch_cycle() -> dict:
    """Runs every enabled watch topic once, ingesting genuinely NEW
    findings (by item_id, tracked in last30days_seen) as intel signals
    (for topics with a symbol) or skipping ingestion otherwise (topic
    still runs and gets deduped/logged, just has nowhere trading-real to
    post to yet -- see module docstring's news/general-topic note in
    the caller). Returns a summary dict for logging/admin visibility."""
    from .routers.intel import ingest_signal_internal

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM last30days_watch WHERE enabled=1")
        topics = [dict(r) for r in await cur.fetchall()]

    summary = {"topics_run": 0, "new_findings": 0, "signals_ingested": 0, "errors": []}
    for t in topics:
        report = await run_topic_raw(t["topic"], lookback_days=t.get("lookback_days") or 30)
        summary["topics_run"] += 1
        if report is None:
            summary["errors"].append(t["topic"])
            continue

        for finding in extract_findings(report):
            item_id = finding.get("item_id") or finding.get("url")
            if await _already_seen(t["topic"], item_id):
                continue
            await _mark_seen(t["topic"], item_id)
            summary["new_findings"] += 1

            if t.get("symbol"):
                try:
                    await ingest_signal_internal(
                        symbol=t["symbol"], source=f"last30days:{finding.get('source', 'web')}",
                        type_="sentiment", conviction=min(1.0, float(finding.get("score") or 0.3)),
                        detail=f"{finding.get('title', '')} — {finding.get('url', '')}"[:500],
                    )
                    summary["signals_ingested"] += 1
                except Exception as e:
                    logger.warning("last30days: signal ingest failed for topic %s: %s", t["topic"], e)

    return summary
