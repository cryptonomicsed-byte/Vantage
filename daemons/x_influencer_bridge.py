#!/usr/bin/env python3
"""Bridge: xAI's x_search (real, live X/Twitter index) -> social_tracker.py's
social_accounts/social_signals pipeline -> influencer_correlation.py's
coordinated-mention detection.

WHY THIS, NOT BROWSER AUTOMATION (real evaluation, 2026-08-30 -- see full
report delivered alongside this file):

Considered and rejected: logging a dedicated account into X via Hermes's
Camoufox browser toolset (real, present, session-persistent -- confirmed
via /usr/local/lib/hermes-agent/tools/browser_camofox.py), following +
bell-enabling tracked handles, and cron-polling the notification feed.
Rejected because X actively detects and bans/locks exactly this
automation fingerprint (login + follow + repeated notification-poll from
one account), regardless of which browser-automation tool drives it or
whether traffic is Tor-wrapped (oniux, also real and present, reduces
IP-correlation risk but does nothing against X's behavioral detection --
the real bottleneck here is account behavior, not source IP). crawl4ai
(also present, real prior use was unrelated media-site scraping via
Playwright, and its own log shows the last real attempts timing out) has
the same underlying exposure. None of the awesome-autonomous-web tools
(Stagehand/Browser-use/Skyvern/etc, or hosted Anchor/Steel/Browserbase)
change this: they are all still browser automation impersonating a human
session, the exact pattern X's detection targets.

Chosen instead: xAI's x_search, a real, hosted, legitimate API
(api.x.ai/v1/responses, the same endpoint Hermes's own
tools/x_search_tool.py already calls) that answers natural-language
queries using xAI's own live X index, filterable by
`allowed_x_handles` (max 10 handles/call). No login, no follow, no
bell-enable, no session to detect or ban -- this is licensed API access
to X's data via xAI's partnership, not scraping. Confirmed via Hermes's
own source: requires either XAI_API_KEY (paid) or a Grok/SuperGrok/X
Premium+ OAuth login (`hermes auth add xai-oauth`) -- NEITHER was
present on this VPS as of this build (confirmed via `hermes auth`
showing no xai/xai-oauth entry), so this is a real, flagged blocker
needing an owner decision, not something worked around.

Real limitation, documented honestly: x_search's from_date/to_date
filters are DAY-granularity only (YYYY-MM-DD), not intra-day timestamps
(confirmed via x_search_tool.py's own _parse_iso_date, which rejects
anything but that format). This daemon therefore queries "today" every
cycle and DEDUPES on the real post_url xAI returns (via a local SQLite
table, same "compute your own dedup state from real observations"
convention sportsbetting_bridge.py already uses) rather than relying on
a clean time-boundary the API can't give us.

Real "degraded" handling, ported directly from Hermes's own tool
(x_search_tool.py's own comment: xAI returns 200 OK with a
model-synthesized answer, indistinguishable from a real one, when its
index has no matching posts for the filters) -- this daemon replicates
that exact check (active filter + zero citations = degraded) and DROPS
the response rather than ingesting a fabricated signal.

Once a real post is found, it's written into social_tracker.py's own
social_accounts/social_signals tables (platform='twitter') using the
SAME shape social_tracker.py's own scan_twitter()/post_signal() already
write -- so backend/influencer_correlation.py (built the prior session)
picks up coordinated-mention detection automatically, with zero new
correlation logic needed here. This daemon's only job is getting real
ticker mentions from tracked handles INTO that existing table.
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (_here, os.path.join(_here, "daemons"), "/opt/ares/Vantage/daemons"):
    if os.path.exists(os.path.join(_candidate, "vantage_signals.py")):
        sys.path.insert(0, _candidate)
        break

XAI_API_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1")
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_MODEL = os.environ.get("XAI_X_SEARCH_MODEL", "grok-4.20-reasoning")

# Real per-call cap enforced by xAI's x_search tool (MAX_HANDLES in
# Hermes's own x_search_tool.py) -- chunk the tracked-handle list rather
# than silently truncating it.
MAX_HANDLES_PER_CALL = 10

TRACKED_HANDLES = [
    h.strip().lstrip("@") for h in os.environ.get("X_TRACKED_HANDLES", "").split(",") if h.strip()
]

INTERVAL = int(os.environ.get("X_INFLUENCER_BRIDGE_INTERVAL", "7200"))  # 2h -- day-granularity filter, no reason to poll faster than a real posting cadence

DB_PATH = os.environ.get("X_INFLUENCER_BRIDGE_DB", "/opt/ares/x_influencer_bridge.db")
VANTAGE_DB = os.environ.get("VANTAGE_DB", "/opt/ares/Vantage/data/vantage.db")

_TICKER_RE = re.compile(r"^[A-Z0-9]{2,15}$")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS seen_posts (
        post_url TEXT PRIMARY KEY,
        seen_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def _chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _x_search(handles: list, query: str) -> dict:
    """One real call to xAI's x_search-backed Responses API, same endpoint
    contract as Hermes's own tools/x_search_tool.py. Returns
    {"answer": str, "citations": [...], "degraded": bool} -- degraded=True
    means DROP this result, it's a synthesized non-answer (see module
    docstring). Raises on missing credentials / hard failures -- caller's
    cycle() wraps this per-chunk so one bad chunk doesn't kill the run."""
    if not XAI_API_KEY:
        raise RuntimeError(
            "XAI_API_KEY not set -- x_search requires either a paid xAI API "
            "key or `hermes auth add xai-oauth` with a Grok/SuperGrok/X "
            "Premium+ login. Neither is configured."
        )
    payload = {
        "model": XAI_MODEL,
        "input": [{"role": "user", "content": query}],
        "tools": [{
            "type": "x_search",
            "allowed_x_handles": handles,
            "from_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }],
        "store": False,
    }
    req = urllib.request.Request(
        f"{XAI_API_BASE}/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())

    answer = str(data.get("output_text") or "").strip()
    if not answer:
        for item in data.get("output", []) or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content", []) or []:
                if content.get("type") in ("output_text", "text"):
                    answer = str(content.get("text") or "").strip()
                    break
    citations = list(data.get("citations") or [])
    # Real degraded check, ported from x_search_tool.py: active filters
    # (we always pass allowed_x_handles + from_date) but zero citations
    # means xAI answered from training data, not its real X index.
    degraded = not citations
    return {"answer": answer, "citations": citations, "degraded": degraded}


_QUERY_TEMPLATE = (
    "For each of these X/Twitter accounts: {handles}. "
    "List any post from TODAY that mentions a specific cryptocurrency "
    "ticker/token (e.g. $XYZ) or names a specific coin/project by name. "
    "Return ONLY a JSON array, no prose, no markdown fences. Each element: "
    '{{"handle": "...", "post_url": "...", "ticker": "...", '
    '"sentiment": "BULLISH|BEARISH|NEUTRAL", "excerpt": "..."}}. '
    "If none of these accounts posted anything matching, return []."
)


def _parse_mentions(answer: str) -> list:
    """Real, defensive JSON extraction -- the model is asked for strict
    JSON but LLM output is never 100% reliable; strip markdown fences if
    present, and treat anything that doesn't parse as zero mentions
    (fail-soft, never crash the cycle on a malformed response)."""
    text = answer.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _ensure_social_account(conn: sqlite3.Connection, handle: str) -> int:
    """Real integration point into social_tracker.py's own schema
    (social_accounts, account_type='tracker', platform='twitter') -- reuses
    the exact table influencer_correlation.py already reads from, rather
    than inventing a parallel store."""
    row = conn.execute(
        "SELECT id FROM social_accounts WHERE platform='twitter' AND username=?",
        (handle.lower(),),
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO social_accounts (platform, username, account_type) VALUES ('twitter', ?, 'tracker')",
        (handle.lower(),),
    )
    conn.commit()
    return cur.lastrowid


def _record_mention(conn: sqlite3.Connection, account_id: int, handle: str, m: dict) -> None:
    ticker = str(m.get("ticker") or "").strip().upper().lstrip("$")
    sentiment = str(m.get("sentiment") or "NEUTRAL").strip().upper()
    if sentiment not in ("BULLISH", "BEARISH", "NEUTRAL"):
        sentiment = "NEUTRAL"
    confidence = 0.7 if sentiment == "BULLISH" else 0.5
    conn.execute(
        """INSERT INTO social_signals
           (account_id, platform, username, ticker, contract_address, sentiment, confidence, post_text, post_url)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (account_id, "twitter", handle.lower(), ticker if _TICKER_RE.match(ticker) else "", "",
         sentiment, confidence, str(m.get("excerpt") or "")[:500], str(m.get("post_url") or "")),
    )
    conn.execute(
        "UPDATE social_accounts SET signal_count = signal_count + 1, last_checked = ? WHERE id = ?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), account_id),
    )
    conn.commit()


def cycle():
    if not TRACKED_HANDLES:
        print("X_TRACKED_HANDLES not set -- nothing to track", flush=True)
        return

    dedupe_conn = _db()
    try:
        vantage_conn = sqlite3.connect(VANTAGE_DB)
    except sqlite3.Error as e:
        print(f"cannot open Vantage DB ({VANTAGE_DB}): {e}", flush=True)
        return

    total_mentions = 0
    total_new = 0
    for chunk in _chunk(TRACKED_HANDLES, MAX_HANDLES_PER_CALL):
        query = _QUERY_TEMPLATE.format(handles=", ".join(f"@{h}" for h in chunk))
        try:
            result = _x_search(chunk, query)
        except RuntimeError as e:
            print(f"  {e}", flush=True)
            return  # no credentials -- no point trying the remaining chunks
        except Exception as e:
            print(f"  x_search chunk error ({chunk}): {e}", flush=True)
            continue

        if result["degraded"]:
            print(f"  chunk {chunk}: degraded response (no real citations), skipped", flush=True)
            continue

        mentions = _parse_mentions(result["answer"])
        total_mentions += len(mentions)
        for m in mentions:
            handle = str(m.get("handle") or "").strip().lstrip("@")
            post_url = str(m.get("post_url") or "").strip()
            if not handle or not post_url:
                continue
            already_seen = dedupe_conn.execute(
                "SELECT 1 FROM seen_posts WHERE post_url=?", (post_url,)
            ).fetchone()
            if already_seen:
                continue
            dedupe_conn.execute(
                "INSERT OR IGNORE INTO seen_posts (post_url, seen_at) VALUES (?, ?)",
                (post_url, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            )
            dedupe_conn.commit()
            account_id = _ensure_social_account(vantage_conn, handle)
            _record_mention(vantage_conn, account_id, handle, m)
            total_new += 1
            print(f"  NEW: @{handle} -> {m.get('ticker') or '?'} ({m.get('sentiment')}) {post_url}", flush=True)

    dedupe_conn.close()
    vantage_conn.close()
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] X influencer bridge: "
        f"{total_mentions} mentions seen, {total_new} new signals recorded",
        flush=True,
    )


if __name__ == "__main__":
    print(f"X Influencer Bridge ({INTERVAL}s cycle, handles={TRACKED_HANDLES})", flush=True)
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error: {e}", flush=True)
        time.sleep(INTERVAL)
