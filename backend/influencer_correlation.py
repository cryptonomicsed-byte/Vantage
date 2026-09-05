"""Multi-influencer coordinated-signal detection — real correlation over
backend/daemons/social_tracker.py's own social_signals table (Twitter/X +
Telegram account watchlist, already live: 1,569 real rows across 8 active
accounts as of 2026-08-30). Answers a question nothing else in this
codebase currently answers: not "did one account mention this token" (that's
already what social_signals/post_signal records), but "did MULTIPLE
DIFFERENT tracked accounts mention the SAME token close together in time" --
a real, independent coordinated-attention signal, distinct from and
complementary to any single account's own conviction/sentiment score.

Confirmed real, not hypothetical: querying production social_signals found
genuine cross-account overlaps already present in the data, including one
pair 19 seconds apart on the same real contract address
(FC8D5Hs59Dx8dJpxdYjqcosk5XNcLaSqFMGqxaGgpump, dontcallmecallss +
pumpfunearlytrending, 2026-08-23 22:14:22/22:14:41) -- this module's job is
to surface that pattern going forward, not detect something that's never
actually happened.

Identifier resolution: prefers contract_address when present (unambiguous,
can't collide across unrelated tokens), falls back to `ticker:<SYMBOL>`
when only a bare $TICKER was extracted (common for pre-CA pump.fun
chatter) -- same address-first-then-symbol precedence
degen_filters.is_major_or_stable already uses elsewhere in this codebase,
for the same reason (symbol collisions are real, address collisions
effectively aren't).

CORRELATION_WINDOW_MINUTES=60: "close together in time" needs a real
number. An hour is generous enough to catch a slow-rolling pump narrative
crossing multiple accounts' posting cadences (these are low-frequency
watchlist accounts, not a firehose) while still meaning something --
two mentions six hours apart are just "this token is popular today", not a
coordinated signal. Not tuned against labeled outcome data (none exists
yet) -- a real, documented, adjustable starting point, not a claimed-optimal
threshold.

Dedup: in-memory, keyed on (identifier -> frozenset of participating
usernames) -- same convention backend/mycelium_bridge.py's own emit_*
functions use (re-emit only when the real value changed, here "a new
account joined this coordination"), not persisted across a process
restart. A restart re-emitting the same still-active cluster once is an
acceptable, low-cost redundancy, same tradeoff mycelium_bridge.py's own
docstring documents for its dedup.

Fail-soft: a Mycelium outage never blocks detection itself; only trace
emission is wrapped in mycelium_bridge's own fail-soft POST.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from .db import get_db
from .mycelium_bridge import post_observation

logger = logging.getLogger(__name__)

CORRELATION_WINDOW_MINUTES = 60
MIN_ACCOUNTS_FOR_SIGNAL = 2
# How far back each scan looks -- must be >= CORRELATION_WINDOW_MINUTES so a
# cluster whose members straddle a scan boundary isn't ever split across two
# scans and undercounted in both. 2x the window is a simple, safe margin.
LOOKBACK_MINUTES = CORRELATION_WINDOW_MINUTES * 2

# {identifier: frozenset(usernames)} -- see module docstring's Dedup section.
_last_emitted: dict[str, frozenset] = {}


def _identifier(row: dict) -> Optional[str]:
    ca = (row.get("contract_address") or "").strip()
    if ca:
        return ca
    ticker = (row.get("ticker") or "").strip().upper()
    if ticker:
        return f"ticker:{ticker}"
    return None


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def find_coordinated_mentions(
    rows: list[dict],
    window_minutes: int = CORRELATION_WINDOW_MINUTES,
    min_accounts: int = MIN_ACCOUNTS_FOR_SIGNAL,
) -> list[dict]:
    """Pure function over a list of real social_signals rows (dicts with
    platform/username/ticker/contract_address/created_at) -> a list of real
    coordinated-mention findings. No I/O, no dedup state -- callers (see
    scan_and_emit below) own persistence/emission decisions; this is the
    testable correlation core.

    Algorithm: group by real identifier (see _identifier), sort each
    group's mentions by time, then a two-pointer sliding window finds every
    maximal run where >=min_accounts DISTINCT usernames fall within
    window_minutes of each other. Only genuinely maximal clusters are
    returned (a window fully contained in a later, larger window for the
    same identifier is not separately reported) -- one finding per real
    coordinated event, not one per qualifying sub-window.

    Each finding: {identifier, symbol, accounts: [...], platforms: [...],
    n_accounts, first_seen, last_seen, span_minutes}."""
    by_id: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        ident = _identifier(row)
        ts = _parse_ts(row.get("created_at", ""))
        if not ident or ts is None or not row.get("username"):
            continue
        by_id[ident].append({**row, "_ts": ts})

    findings: list[dict] = []
    window = timedelta(minutes=window_minutes)

    for ident, mentions in by_id.items():
        mentions.sort(key=lambda r: r["_ts"])
        n = len(mentions)
        left = 0
        best_end = -1  # rightmost index already covered by an emitted cluster
        for right in range(n):
            while mentions[right]["_ts"] - mentions[left]["_ts"] > window:
                left += 1
            if right <= best_end:
                continue  # already part of a cluster just emitted for this identifier
            window_slice = mentions[left:right + 1]
            usernames = {m["username"] for m in window_slice}
            if len(usernames) < min_accounts:
                continue
            # Extend right as far as possible while staying within
            # window_minutes of THIS cluster's own start, so the finding
            # captures the real maximal group, not just the first
            # threshold-crossing pair.
            end = right
            while end + 1 < n and mentions[end + 1]["_ts"] - window_slice[0]["_ts"] <= window:
                end += 1
                usernames.add(mentions[end]["username"])
            final_slice = mentions[left:end + 1]
            findings.append({
                "identifier": ident,
                "symbol": final_slice[0].get("ticker") or None,
                "accounts": sorted({m["username"] for m in final_slice}),
                "platforms": sorted({m.get("platform", "") for m in final_slice if m.get("platform")}),
                "n_accounts": len({m["username"] for m in final_slice}),
                "first_seen": final_slice[0]["_ts"].strftime("%Y-%m-%d %H:%M:%S"),
                "last_seen": final_slice[-1]["_ts"].strftime("%Y-%m-%d %H:%M:%S"),
                "span_minutes": round((final_slice[-1]["_ts"] - final_slice[0]["_ts"]).total_seconds() / 60.0, 1),
            })
            best_end = end

    return findings


def _emit(finding: dict) -> bool:
    """Emit one real observation trace for a coordinated-mention finding,
    deduped so re-scanning an unchanged cluster doesn't spam (see module
    docstring). Returns True if a trace was actually sent (new or grown
    cluster), False if deduped or the post itself failed."""
    ident = finding["identifier"]
    account_set = frozenset(finding["accounts"])
    if _last_emitted.get(ident) == account_set:
        return False
    ok = post_observation(
        agent="influencer_correlation",
        session="coordinated-signal-scan",
        action="coordinated_mention",
        target=ident,
        payload=finding,
    )
    if ok:
        _last_emitted[ident] = account_set
    return ok


async def scan_and_emit(db: Optional[aiosqlite.Connection] = None) -> list[dict]:
    """Real end-to-end scan: pull the last LOOKBACK_MINUTES of
    social_signals, run find_coordinated_mentions, emit a trace per new/
    grown cluster. Returns the real findings list (regardless of whether
    each one was actually a NEW emission) so a caller/test can inspect
    what was detected this scan, not just what changed.

    `db` is accepted (not just opened internally) so tests and any future
    caller running inside an existing get_db() context don't open a second
    connection -- opens its own via get_db() when not provided."""
    async def _run(conn: aiosqlite.Connection) -> list[dict]:
        conn.row_factory = aiosqlite.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await conn.execute(
            """SELECT platform, username, ticker, contract_address, created_at
               FROM social_signals WHERE created_at > ?""",
            (cutoff,),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        findings = find_coordinated_mentions(rows)
        for f in findings:
            try:
                _emit(f)
            except Exception:
                logger.exception("influencer_correlation: emit failed for %s", f.get("identifier"))
        return findings

    if db is not None:
        return await _run(db)
    async with get_db() as conn:
        return await _run(conn)
