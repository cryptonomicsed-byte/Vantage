"""P4-08 / P4-10 / P4-13 -- guild router: guild agents' intel into Vantage.

Guild agents (Grokbot, Muse, Q) hold no keys and never write to Vantage
tables directly. They claim a trading-space task (P1-12 lease), then submit
an ``intel_record/1`` here. The router checks it, keeps it, routes it, and
files it on the task through the existing submit path -- the space owner's
existing accept/reject closes the round trip.

intel_record/1
  {"schema": "intel_record/1", "lane": "<lane>.<kind>", "items": [ {...} ]}

Lanes (P4-10). Who may use a lane is an allowlist of agent ids/names:
  x.*         Grokbot  GUILD_LANE_X_AGENTS          (default: grokbot)
  news.*      Muse     GUILD_LANE_NEWS_AGENTS       (default: empty)
  research.*  Q        GUILD_LANE_RESEARCH_AGENTS   (default: empty)
Muse/Q access is open decision 3, so their allowlists ship empty: the lanes
exist and are validated, nobody can post to them until someone is listed.

Router checks (P4-08 for x.*):
  every lane: schema, lane known, caller a guild member on the lane
    allowlist, the caller holds the task's live claim, 1..50 items,
    text <= 4000 chars, no duplicate of an item already routed
  x.*:     post_url is an x.com/twitter.com status URL; username matches the
           URL's handle; posted_at present, not > 5 min in the future, not
           older than 7 days; contract_address, if present, is base58 32-44
           chars (exact contract -- a ticker is never turned into one)
  news.*:  url http(s), title, published_at (same time rules)
  research.*: title, body
Routing:
  x.*        -> social_signals (platform x, username, ticker, contract,
                post_text, post_url, signal_type) + posted_at (new column,
                P4-08: confluence and anything else can use the time the
                post was made instead of when it was scraped)
  news.* / research.* -> kept in guild_intel_records only
Every record (accepted or rejected, with reasons) -> guild_intel_records.
Nothing here posts to the signal pool or touches trading.

Schedules (P4-13): GUILD_ROUTER_SCHEDULES points to a JSON list of
  {"guild": slug, "space_id": id, "lane": "x.mentions", "title": "...",
   "every_minutes": 60, "description": "..."}
Each due entry creates a proposed task in that space, created by the
space's existing owner (no new owner is invented). The default is no file
-> no schedules (which schedules to run is open decision 3).

Flag GUILD_ROUTER_ENABLED (default off): off -> submissions get 503 and the
scheduler does nothing. Reads (lanes, records) always work.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCHEMA = "intel_record/1"
MAX_ITEMS = 50
MAX_TEXT = 4000
FUTURE_SLACK_S = 300
MAX_AGE_S = 7 * 86400
LANES = {
    "x": {"env": "GUILD_LANE_X_AGENTS", "default": "grokbot", "who": "Grokbot"},
    "news": {"env": "GUILD_LANE_NEWS_AGENTS", "default": "", "who": "Muse"},
    "research": {"env": "GUILD_LANE_RESEARCH_AGENTS", "default": "", "who": "Q"},
}
_X_URL = re.compile(r"^https://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d{5,25})(?:[/?#].*)?$")
_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_HTTP = re.compile(r"^https?://[^\s]+$")

DDL = (
    """CREATE TABLE IF NOT EXISTS guild_intel_records (
  id TEXT PRIMARY KEY, guild_slug TEXT, space_id TEXT, task_id TEXT,
  agent_id INTEGER, lane TEXT, status TEXT NOT NULL, reasons TEXT NOT NULL DEFAULT '[]',
  items INTEGER NOT NULL DEFAULT 0, routed INTEGER NOT NULL DEFAULT 0,
  record TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_gir_space ON guild_intel_records(space_id, created_at)",
    """CREATE TABLE IF NOT EXISTS guild_intel_items (
  dedupe_key TEXT PRIMARY KEY, record_id TEXT NOT NULL, lane TEXT NOT NULL,
  item TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS guild_router_schedule_runs (
  schedule_key TEXT PRIMARY KEY, last_run_at INTEGER NOT NULL, last_task_id TEXT)""",
)


def enabled() -> bool:
    return os.environ.get("GUILD_ROUTER_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def lane_allowlist(lane_root: str) -> set:
    cfg = LANES[lane_root]
    raw = os.environ.get(cfg["env"], cfg["default"])
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def lanes_view() -> List[Dict[str, Any]]:
    return [{"lane": f"{k}.*", "agent": v["who"], "allowlist": sorted(lane_allowlist(k)),
             "open": bool(lane_allowlist(k))} for k, v in LANES.items()]


async def ensure_tables(db) -> None:
    for d in DDL:
        await db.execute(d)
    # P4-08: the time the post was made (tolerant ALTER; created_at stays the
    # time it was stored).
    try:
        await db.execute("ALTER TABLE social_signals ADD COLUMN posted_at TEXT")
    except Exception:  # noqa: BLE001 -- already there, or table not created yet
        pass


def _parse_time(v: Any) -> Optional[int]:
    if isinstance(v, (int, float)) and v > 0:
        return int(v / 1000) if v > 1e12 else int(v)
    if isinstance(v, str) and v.strip():
        s = v.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return None


def _time_ok(ts: Optional[int], now: int, name: str) -> Optional[str]:
    if ts is None:
        return f"{name} missing or unparseable"
    if ts > now + FUTURE_SLACK_S:
        return f"{name} is in the future"
    if ts < now - MAX_AGE_S:
        return f"{name} older than 7 days"
    return None


def check_item(lane: str, it: Any, now: int) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str]]:
    """(normalised item, reasons, dedupe key). Reasons empty = OK."""
    if not isinstance(it, dict):
        return None, ["item is not an object"], None
    root = lane.split(".", 1)[0]
    errs: List[str] = []
    for k, v in it.items():
        if isinstance(v, str) and len(v) > MAX_TEXT:
            errs.append(f"{k} longer than {MAX_TEXT} chars")
    if root == "x":
        url = str(it.get("post_url") or "")
        m = _X_URL.match(url)
        if not m:
            errs.append("post_url is not an x.com/twitter.com status URL")
        user = str(it.get("username") or "").lstrip("@")
        if m and user.lower() != m.group(1).lower():
            errs.append("username does not match the post URL")
        ts = _parse_time(it.get("posted_at"))
        e = _time_ok(ts, now, "posted_at")
        if e:
            errs.append(e)
        ca = str(it.get("contract_address") or "").strip()
        if ca and not _B58.match(ca):
            errs.append("contract_address is not an exact base58 contract")
        if errs:
            return None, errs, None
        norm = {"post_url": url, "username": user, "status_id": m.group(2), "posted_at": ts,
                "text": str(it.get("text") or ""), "contract_address": ca or None,
                "ticker": (str(it.get("ticker") or "").lstrip("$").upper()[:20] or None),
                "sentiment": (str(it.get("sentiment") or "").upper() or None),
                "signal_type": str(it.get("signal_type") or "mention")[:20]}
        return norm, [], f"x:{m.group(2)}"
    if root == "news":
        url = str(it.get("url") or "")
        if not _HTTP.match(url):
            errs.append("url must be http(s)")
        if not str(it.get("title") or "").strip():
            errs.append("title required")
        ts = _parse_time(it.get("published_at"))
        e = _time_ok(ts, now, "published_at")
        if e:
            errs.append(e)
        if errs:
            return None, errs, None
        return {"url": url, "title": str(it["title"]).strip(), "published_at": ts,
                "source": str(it.get("source") or "")}, [], f"news:{url}"
    if root == "research":
        if not str(it.get("title") or "").strip():
            errs.append("title required")
        if not str(it.get("body") or "").strip():
            errs.append("body required")
        if errs:
            return None, errs, None
        refs = [str(r) for r in (it.get("refs") or []) if isinstance(r, (str, int))][:20]
        import hashlib
        key = "research:" + hashlib.sha256((it["title"].strip() + "\x00" + it["body"].strip()).encode()).hexdigest()
        return {"title": str(it["title"]).strip(), "body": str(it["body"]), "refs": refs}, [], key
    return None, [f"unknown lane {lane}"], None


def check_record(rec: Any, now: int) -> Tuple[List[Tuple[Dict[str, Any], str]], List[str], str]:
    """(ok items with dedupe keys, record-level + item reasons, lane)."""
    if not isinstance(rec, dict):
        return [], ["record is not an object"], ""
    reasons: List[str] = []
    lane = str(rec.get("lane") or "")
    if rec.get("schema") != SCHEMA:
        reasons.append(f"schema must be {SCHEMA}")
    if lane.split(".", 1)[0] not in LANES or "." not in lane:
        reasons.append("lane must be x.*, news.* or research.*")
    items = rec.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        reasons.append(f"items must be a list of 1..{MAX_ITEMS}")
    if reasons:
        return [], reasons, lane
    ok = []
    for i, it in enumerate(items):
        norm, errs, key = check_item(lane, it, now)
        if errs:
            reasons.extend(f"item {i}: {e}" for e in errs)
        else:
            ok.append((norm, key))
    return ok, reasons, lane


def agent_on_lane(agent: dict, lane: str) -> bool:
    allow = lane_allowlist(lane.split(".", 1)[0])
    return bool(allow) and (str(agent.get("id")) in allow or str(agent.get("name") or "").lower() in allow)


async def _route_item(db, lane: str, norm: Dict[str, Any]) -> None:
    if lane.startswith("x."):
        posted = datetime.fromtimestamp(norm["posted_at"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "INSERT INTO social_signals (platform, username, ticker, contract_address, sentiment, post_text, "
            "post_url, signal_type, posted_at) VALUES ('x',?,?,?,?,?,?,?,?)",
            (norm["username"], norm["ticker"], norm["contract_address"], norm["sentiment"],
             norm["text"][:MAX_TEXT], norm["post_url"], norm["signal_type"], posted))


async def submit(slug: str, space_id: str, task_id: str, record: Any, agent: dict,
                 now: Optional[int] = None) -> Dict[str, Any]:
    """Validate + store + route + file on the task. Raises HTTPException on
    a request that can't be filed (not on the lane, no claim, bad record)."""
    from fastapi import HTTPException

    from backend.db import get_db
    from backend.routers.alpha import _SOCIAL_SIGNALS_DDL
    from backend.routers import trading_space as ts
    now = int(now or time.time())
    if not enabled():
        raise HTTPException(503, "guild router is off (GUILD_ROUTER_ENABLED)")
    await ts._assert_guild_member(slug, agent)
    await ts._get_space(space_id, slug)
    ok, reasons, lane = check_record(record, now)
    rid = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(_SOCIAL_SIGNALS_DDL)
        await ensure_tables(db)
        if lane and lane.split(".", 1)[0] in LANES and not agent_on_lane(agent, lane):
            reasons.insert(0, f"agent is not on the {lane.split('.', 1)[0]}.* lane allowlist")
            ok = []
        dupes = []
        fresh = []
        for norm, key in ok:
            if await (await db.execute("SELECT 1 FROM guild_intel_items WHERE dedupe_key=?", (key,))).fetchone():
                dupes.append(key)
            else:
                fresh.append((norm, key))
        if dupes and not fresh and not reasons:
            reasons.append("every item was already routed")
        status = "rejected" if reasons and not fresh else ("partial" if reasons or dupes else "accepted")
        await db.execute(
            "INSERT INTO guild_intel_records (id, guild_slug, space_id, task_id, agent_id, lane, status, reasons, "
            "items, routed, record, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, slug, space_id, task_id, agent.get("id"), lane, status,
             json.dumps(reasons + [f"duplicate {d}" for d in dupes]),
             len(record.get("items") or []) if isinstance(record, dict) else 0, len(fresh),
             json.dumps(record)[:200_000], now))
        if status == "rejected":
            await db.commit()
            raise HTTPException(422, {"record_id": rid, "status": status, "reasons": reasons})
        for norm, key in fresh:
            await _route_item(db, lane, norm)
            await db.execute("INSERT INTO guild_intel_items (dedupe_key, record_id, lane, item, created_at) "
                             "VALUES (?,?,?,?,?)", (key, rid, lane, json.dumps(norm), now))
        await db.commit()
    # file it on the task through the existing submit path (claimer + lease checks)
    filed = await ts.submit_task(slug, space_id, task_id, artifact_kind="intel_record",
                                 title=f"{lane}: {len(fresh)} item(s)",
                                 content_text=json.dumps({"record_id": rid, "lane": lane, "record": record})[:200_000],
                                 content_uri="", agent=agent)
    return {"record_id": rid, "status": status, "routed": len(fresh), "duplicates": len(dupes),
            "reasons": reasons, "artifact_id": filed.get("artifact_id")}


async def recent(space_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    from backend.db import get_db
    async with get_db() as db:
        await ensure_tables(db)
        q = ("SELECT id, guild_slug, space_id, task_id, agent_id, lane, status, reasons, items, routed, created_at "
             "FROM guild_intel_records")
        args: list = []
        if space_id:
            q += " WHERE space_id=?"
            args.append(space_id)
        cols = ["id", "guild_slug", "space_id", "task_id", "agent_id", "lane", "status", "reasons", "items",
                "routed", "created_at"]
        rows = [dict(zip(cols, r)) for r in await (await db.execute(
            q + " ORDER BY created_at DESC LIMIT ?", (*args, limit))).fetchall()]
    for r in rows:
        r["reasons"] = json.loads(r["reasons"])
    return {"enabled": enabled(), "lanes": lanes_view(), "schedules": load_schedules()[0], "records": rows}


# ── P4-13 schedules ─────────────────────────────────────────────────────────

def load_schedules(path: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(valid schedules, problems). No file configured -> ([], [])."""
    path = path if path is not None else os.environ.get("GUILD_ROUTER_SCHEDULES", "").strip()
    if not path:
        return [], []
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return [], [f"cannot read {path}: {e}"]
    if not isinstance(data, list):
        return [], ["schedule file must be a JSON list"]
    out, probs = [], []
    for i, s in enumerate(data):
        if not isinstance(s, dict):
            probs.append(f"entry {i}: not an object")
            continue
        lane = str(s.get("lane") or "")
        every = s.get("every_minutes")
        miss = [k for k in ("guild", "space_id", "lane", "title") if not str(s.get(k) or "").strip()]
        if miss:
            probs.append(f"entry {i}: missing {', '.join(miss)}")
        elif lane.split(".", 1)[0] not in LANES or "." not in lane:
            probs.append(f"entry {i}: unknown lane {lane}")
        elif not isinstance(every, int) or not 5 <= every <= 7 * 1440:
            probs.append(f"entry {i}: every_minutes must be an int in 5..10080")
        else:
            out.append({"guild": s["guild"], "space_id": s["space_id"], "lane": lane, "title": s["title"],
                        "every_minutes": every, "description": str(s.get("description") or ""),
                        "key": f"{s['guild']}|{s['space_id']}|{lane}|{s['title']}"})
    return out, probs


async def run_schedules(now: Optional[int] = None, path: Optional[str] = None) -> Dict[str, Any]:
    """Create a proposed task for each due schedule. Never raises."""
    from backend.db import get_db
    now = int(now or time.time())
    scheds, probs = load_schedules(path)
    stats: Dict[str, Any] = {"due": 0, "created": 0, "skipped": 0, "problems": probs}
    if not scheds:
        return stats
    try:
        async with get_db() as db:
            await ensure_tables(db)
            for s in scheds:
                last = await (await db.execute("SELECT last_run_at FROM guild_router_schedule_runs "
                                               "WHERE schedule_key=?", (s["key"],))).fetchone()
                if last and now - last[0] < s["every_minutes"] * 60:
                    continue
                stats["due"] += 1
                owner = await (await db.execute(
                    "SELECT created_by_agent_id FROM trading_spaces WHERE id=? AND guild_slug=?",
                    (s["space_id"], s["guild"]))).fetchone()
                if not owner or owner[0] is None:
                    stats["skipped"] += 1
                    stats["problems"].append(f"{s['key']}: space not found or has no owner")
                    continue
                tid = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO vantage_tasks (id, workspace_id, guild_slug, title, description, status, priority, "
                    "created_by_agent_id, kind_tag, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, s["space_id"], s["guild"], s["title"], s["description"], "proposed", 50, owner[0],
                     f"lane:{s['lane']}", now, now))
                await db.execute("INSERT OR REPLACE INTO guild_router_schedule_runs (schedule_key, last_run_at, "
                                 "last_task_id) VALUES (?,?,?)", (s["key"], now, tid))
                stats["created"] += 1
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("guild router schedules failed (fail-open): %s", e)
        stats["error"] = str(e)[:200]
    return stats


async def schedule_loop(interval_s: int = 60) -> None:
    while True:
        await asyncio.sleep(interval_s)
        if not enabled():
            continue
        out = await run_schedules()
        if out["created"] or out["problems"]:
            logger.info("guild router schedules: %s", out)
