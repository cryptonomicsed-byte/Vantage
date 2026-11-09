"""P3-01 -- Crucible trading adapter.

Vantage signals become Crucible claims; confirmations become attestations;
the resolver's outcome becomes the oracle verdict.

  signal_ledger row (first for its asset+direction)  -> 47001 claim
  same asset + direction within 30 min, any source   -> 47002 attestation on THAT claim
  Mycelium signal_outcome mark for the claim's record -> 47010 oracle verdict

Field mapping (signal_ledger / signal_record/1 -> claim):
  community ``c``     CRUCIBLE_COMMUNITY (default vantage-trading)
  domain              ``trading``
  confidence ``conf`` conviction (as capped at ingest), clamped into (0, 1)
  half_life           horizon_hours * 3600 (>= 1h)
  expiry              created_at + window + 6h; the oracle verdict overrides decay anyway
  inputs              {record_id, source, chain, address, symbol, direction,
                       entry_price_usd, window, threshold_pct} -- the experiment id
                      therefore binds the verdict to this exact record and rule
  falsifier           signal-outcome.wat (observational, key ``signal:outcome``:
                      hit -> holds, miss -> fails, absent -> indeterminate)
  lineage / env       ``source:<source>`` / ``vantage``; guild agents:
                      ``guild_agent:<pseudonym>`` / ``guild-router``
  extra tags          ``signal`` = ledger id, ``attested`` = true|false, ``via``

Only long/short records become claims: the resolver logs hold/exit/alert but
does not score them, so a claim about one could never be settled.

Who signs:
  * daemon / service sources -> that source's own key (per-source key file on
    the VPS), ``attested`` true;
  * guild agents (producer.kind guild_agent) -> the guild router's key, tagged
    ``attested`` false and ``via`` guild-router: the router relays the agent's
    call, it does not vouch for the agent;
  * oracle verdicts -> the resolver key (P1-17). Never a source key: an oracle
    that is also an attestor buys nothing (Crucible SPEC, 47010).
A record whose signer key is missing is left unprocessed (retried each cycle
while it is inside the lookback) -- never signed with a substitute key.

Verdict: the window's ``hit`` (true -> holds, false -> fails). An unscored or
unpriced mark (hit null) would be an ``indeterminate`` verdict, which settles
nothing in Crucible, so it is recorded locally and not signed.

Flag CRUCIBLE_ADAPTER_ENABLED, default off (read each cycle). Crucible is not
installed on the box: with no ``crucible`` CLI the adapter does nothing and
says so -- it fails open, and ingest never waits on it (it reads the ledger
after the fact).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

from backend import crucible_events as ce

logger = logging.getLogger(__name__)

FLAG = "CRUCIBLE_ADAPTER_ENABLED"
MATCH_WINDOW_S = 30 * 60
LOOKBACK_S = 24 * 3600
INTERVAL_S = 60
BATCH = 200
FALSIFIER = "signal-outcome.wat"
MANIFEST = {"observations": ["signal:outcome"], "fuel": 50_000_000, "memory_pages": 64, "max_output": 8192}
DOMAIN = "trading"
WINDOWS_S = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}
HIT_THRESHOLD_PCT = 1.0  # the resolver's (mycelium P2-03) default threshold
SCORED = ("long", "short")

DDL = (
    """CREATE TABLE IF NOT EXISTS crucible_claims (
  claim_id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL UNIQUE,
  match_key TEXT NOT NULL,
  source TEXT, pubkey TEXT NOT NULL, attested INTEGER NOT NULL DEFAULT 1,
  experiment TEXT NOT NULL, module_digest TEXT, inputs TEXT NOT NULL,
  window TEXT NOT NULL, created_at INTEGER NOT NULL,
  attestations INTEGER NOT NULL DEFAULT 0,
  verdict_outcome TEXT, verdict_event_id TEXT, verdict_at INTEGER
)""",
    "CREATE INDEX IF NOT EXISTS idx_crucible_claims_match ON crucible_claims(match_key, created_at)",
    """CREATE TABLE IF NOT EXISTS crucible_record_map (
  record_id TEXT PRIMARY KEY,
  claim_id TEXT, role TEXT NOT NULL, reason TEXT, processed_at INTEGER NOT NULL
)""",
)


def enabled() -> bool:
    return ce.flag(FLAG)


def window_label() -> str:
    w = os.environ.get("CRUCIBLE_VERDICT_WINDOW", "24h").strip()
    return w if w in WINDOWS_S else "24h"


async def ensure_tables(db) -> None:
    await ce.ensure_outbox(db)
    for ddl in DDL:
        await db.execute(ddl)


# ── mapping ──────────────────────────────────────────────────────────────────

def _json(v: Any, default: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v if v is not None else default


def match_key(row: dict) -> str:
    """Same asset + same direction. The address when there is one (a mint is
    never matched by ticker), else chain + symbol for majors."""
    chain = (row.get("chain") or "").lower()
    addr = (row.get("address") or "").strip()
    asset = addr if addr else f"sym:{(row.get('symbol') or '').upper()}"
    return f"{chain}|{asset}|{(row.get('direction') or '').lower()}"


def is_guild(row: dict) -> bool:
    prod = _json(row.get("producer"), {})
    return isinstance(prod, dict) and prod.get("kind") == "guild_agent"


def signer_for(row: dict) -> tuple[Optional[bytes], bool]:
    """(secret, attested). Guild agents go through the router, attested false."""
    if is_guild(row):
        return ce.router_key(), False
    return ce.source_key(str(row.get("source") or "")), True


def lineage_env(row: dict) -> tuple[str, str]:
    if is_guild(row):
        prod = _json(row.get("producer"), {})
        try:
            from backend.mycelium_privacy import pseudonymize_agent
            who = pseudonymize_agent(str(prod.get("agent_id") or ""))
        except Exception:
            who = "unknown"
        return f"guild_agent:{who}", "guild-router"
    return f"source:{row.get('source')}", "vantage"


def confidence(row: dict) -> float:
    c = row.get("conviction")
    try:
        c = float(c)
    except (TypeError, ValueError):
        c = 0.5
    return min(0.99, max(0.01, c))


def claim_inputs(row: dict, window: str) -> dict:
    entry = row.get("entry_price_usd")
    return {
        "record_id": row["id"], "source": row.get("source") or "",
        "chain": row.get("chain") or "", "address": row.get("address") or "",
        "symbol": row.get("symbol") or "", "direction": (row.get("direction") or "").lower(),
        "entry_price_usd": float(entry) if isinstance(entry, (int, float)) else None,
        "window": window, "threshold_pct": HIT_THRESHOLD_PCT,
    }


def statement(inp: dict) -> str:
    asset = f"{inp['chain']}:{inp['address']}" if inp["address"] else inp["chain"] or "?"
    return (f"{inp['symbol'] or '?'} {inp['direction']} ({asset}): signed return beats "
            f"+{inp['threshold_pct']}% at +{inp['window']} from entry")


def event_time(row: dict, now: int) -> int:
    t = row.get("created_at") or row.get("ingested_at") or now
    try:
        t = int(t)
    except (TypeError, ValueError):
        t = now
    return min(t, now)  # a claim from the future is refused by Crucible


def _extra_tags(row: dict, attested: bool) -> list[list[str]]:
    tags = [["signal", str(row["id"])], ["attested", "true" if attested else "false"]]
    if not attested:
        tags.append(["via", "guild-router"])
    return tags


# ── one pass: ledger rows -> claims / attestations ───────────────────────────

async def _unprocessed(db, since: int, limit: int) -> list[dict]:
    import aiosqlite
    db.row_factory = aiosqlite.Row
    rows = await (await db.execute(
        "SELECT l.* FROM signal_ledger l LEFT JOIN crucible_record_map m ON m.record_id = l.id "
        "WHERE m.record_id IS NULL AND l.ingested_at >= ? "
        "ORDER BY COALESCE(l.created_at, l.ingested_at), l.rowid LIMIT ?", (since, limit))).fetchall()
    db.row_factory = None
    return [dict(r) for r in rows]


async def _open_claim(db, key: str, at: int) -> Optional[dict]:
    """The claim this row confirms: same key, created in the 30 min before it."""
    import aiosqlite
    db.row_factory = aiosqlite.Row
    r = await (await db.execute(
        "SELECT * FROM crucible_claims WHERE match_key=? AND created_at <= ? AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1", (key, at, at - MATCH_WINDOW_S))).fetchone()
    db.row_factory = None
    return dict(r) if r else None


async def _mark(db, record_id: str, role: str, claim_id: Optional[str] = None, reason: str = "") -> None:
    await db.execute("INSERT OR IGNORE INTO crucible_record_map (record_id, claim_id, role, reason, "
                     "processed_at) VALUES (?,?,?,?,?)", (record_id, claim_id, role, reason or None,
                                                          int(time.time())))


def build_claim(row: dict, secret: bytes, attested: bool, window: str, now: int) -> Optional[tuple[dict, dict]]:
    """(signed claim, {experiment, module_digest, inputs}) or None."""
    inp = claim_inputs(row, window)
    lineage, env = lineage_env(row)
    at = event_time(row, now)
    horizon = row.get("horizon_hours") or 24
    try:
        half_life = max(3600, int(float(horizon) * 3600))
    except (TypeError, ValueError):
        half_life = 24 * 3600
    built = ce.cli("claim.build", {
        "pubkey": ce.pubkey_hex(secret), "created_at": at, "community": ce.community(),
        "domain": DOMAIN, "statement": statement(inp),
        "rationale": f"signal_ledger {row['id']} ({row.get('type') or 'signal'})",
        "confidence": confidence(row), "half_life": half_life,
        "expiry": at + WINDOWS_S[window] + 6 * 3600, "inputs": inp,
        "manifest": MANIFEST, "module_path": FALSIFIER, "lineage": lineage, "env": env,
    })
    if not built or not isinstance(built.get("event"), dict) or not built.get("experiment"):
        return None
    ev = ce.sign(ce.add_tags(built["event"], _extra_tags(row, attested)), secret)
    return ev, {"experiment": built["experiment"], "module_digest": built.get("module_digest"), "inputs": inp}


def build_attestation(row: dict, claim: dict, secret: bytes, attested: bool, now: int) -> Optional[dict]:
    """The confirming source runs the claim's falsifier on its own call: it
    observed ``hit``. settle() later scores that against the oracle."""
    lineage, env = lineage_env(row)
    req = {
        "manifest": MANIFEST, "module_path": FALSIFIER, "inputs": json.loads(claim["inputs"]),
        "observations": {"signal:outcome": "hit"}, "claim": claim["claim_id"],
        "experiment": claim["experiment"], "pubkey": ce.pubkey_hex(secret),
        "created_at": event_time(row, now), "lineage": lineage, "env": env, "blind": False,
    }
    if claim.get("module_digest"):
        req["module_digest"] = claim["module_digest"]
    out = ce.cli("probe.run", req)
    if not out or not isinstance(out.get("attestation"), dict):
        return None
    return ce.sign(ce.add_tags(out["attestation"], _extra_tags(row, attested)), secret)


async def process_once(limit: int = BATCH, now: Optional[int] = None) -> dict:
    """One pass over new ledger rows. Never raises."""
    stats = {"claims": 0, "attestations": 0, "skipped": 0, "no_key": 0, "failed": 0, "crucible": "ok"}
    if not ce.cli_available():
        stats["crucible"] = "unavailable"
        return stats
    now = int(now or time.time())
    window = window_label()
    from backend.db import get_db
    from backend.signal_ledger import ensure_tables as ensure_ledger
    try:
        async with get_db() as db:
            await ensure_ledger(db)
            await ensure_tables(db)
            rows = await _unprocessed(db, now - LOOKBACK_S, limit)
            for row in rows:
                direction = (row.get("direction") or "").lower()
                if direction not in SCORED:
                    await _mark(db, row["id"], "skipped", reason=f"unscored direction {direction or 'none'}")
                    stats["skipped"] += 1
                    continue
                secret, attested = signer_for(row)
                if secret is None:
                    stats["no_key"] += 1  # left unprocessed: a key may be installed later
                    continue
                key = match_key(row)
                claim = await _open_claim(db, key, event_time(row, now))
                if claim:
                    ev = await asyncio.to_thread(build_attestation, row, claim, secret, attested, now)
                    if ev is None:
                        stats["failed"] += 1
                        continue
                    await ce.store(db, ev, claim_id=claim["claim_id"], record_id=row["id"],
                                   source=row.get("source") or "", attested=attested)
                    await db.execute("UPDATE crucible_claims SET attestations = attestations + 1 "
                                     "WHERE claim_id=?", (claim["claim_id"],))
                    await _mark(db, row["id"], "attestation", claim["claim_id"])
                    stats["attestations"] += 1
                else:
                    built = await asyncio.to_thread(build_claim, row, secret, attested, window, now)
                    if built is None:
                        stats["failed"] += 1
                        continue
                    ev, meta = built
                    await ce.store(db, ev, claim_id=ev["id"], record_id=row["id"],
                                   source=row.get("source") or "", attested=attested)
                    await db.execute(
                        "INSERT OR IGNORE INTO crucible_claims (claim_id, record_id, match_key, source, pubkey, "
                        "attested, experiment, module_digest, inputs, window, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (ev["id"], row["id"], key, row.get("source"), ev["pubkey"], 1 if attested else 0,
                         meta["experiment"], meta["module_digest"], json.dumps(meta["inputs"]), window,
                         ev["created_at"]))
                    await _mark(db, row["id"], "claim", ev["id"])
                    stats["claims"] += 1
                await db.commit()  # per row: a crash mid-batch never loses a signed event
    except Exception as e:
        logger.warning("crucible adapter pass failed (fail-open): %s", e)
        stats["error"] = str(e)[:200]
    return stats


# ── verdicts from the Mycelium resolver ──────────────────────────────────────

def _iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def fetch_outcomes(since_ts: int, window: str) -> Optional[dict[str, dict]]:
    """record id -> signal_outcome payload for ``window``; None if Mycelium is
    unreachable. Shape (mycelium P2-03): {window, price, ret_pct,
    signed_ret_pct, hit, scored, late, marked_at, threshold_pct, ...}."""
    from backend.mycelium_bridge import MYCELIUM_URL
    q = urllib.parse.urlencode({"agent": "vantage_ledger", "action": "signal_outcome",
                                "since": _iso(since_ts), "limit": 5000})
    try:
        with urllib.request.urlopen(f"{MYCELIUM_URL}/api/traces?{q}", timeout=10) as r:
            body = json.loads(r.read() or b"{}")
    except Exception:
        return None
    out: dict[str, dict] = {}
    for t in body.get("traces") or []:
        if not isinstance(t, dict) or not t.get("target"):
            continue
        p = _json(t.get("payload"), {})
        if isinstance(p, dict) and p.get("window") == window:
            out.setdefault(t["target"], p)  # first mark wins; the resolver never re-marks
    return out


def outcome_of(mark: dict) -> str:
    if not mark.get("scored") and mark.get("scored") is not None:
        return "indeterminate"
    hit = mark.get("hit")
    if hit is True:
        return "holds"
    if hit is False:
        return "fails"
    return "indeterminate"


async def verdict_once(now: Optional[int] = None,
                       fetch: Callable[[int, str], Optional[dict]] = fetch_outcomes) -> dict:
    """Sign a 47010 for every claim whose window has been marked. Never raises."""
    stats = {"verdicts": 0, "indeterminate": 0, "waiting": 0, "crucible": "ok"}
    if not ce.cli_available():
        stats["crucible"] = "unavailable"
        return stats
    secret = ce.resolver_key()
    if secret is None:
        stats["crucible"] = "no_resolver_key"
        return stats
    now = int(now or time.time())
    from backend.db import get_db
    import aiosqlite
    try:
        async with get_db() as db:
            await ensure_tables(db)
            db.row_factory = aiosqlite.Row
            due = [dict(r) for r in await (await db.execute(
                "SELECT * FROM crucible_claims WHERE verdict_outcome IS NULL ORDER BY created_at LIMIT 500"
            )).fetchall()]
            db.row_factory = None
            due = [c for c in due if c["created_at"] + WINDOWS_S.get(c["window"], 86400) <= now]
            if not due:
                return stats
            marks: dict[str, dict] = {}
            for w in {c["window"] for c in due}:
                got = await asyncio.to_thread(fetch, min(c["created_at"] for c in due) - 3600, w)
                if got is None:
                    stats["crucible"] = "mycelium_unreachable"
                    return stats
                marks.update({(rid, w): p for rid, p in got.items()})
            pk = ce.pubkey_hex(secret)
            for c in due:
                mark = marks.get((c["record_id"], c["window"]))
                if mark is None:
                    stats["waiting"] += 1
                    continue
                outcome = outcome_of(mark)
                if outcome == "indeterminate":
                    await db.execute("UPDATE crucible_claims SET verdict_outcome='indeterminate', verdict_at=? "
                                     "WHERE claim_id=?", (now, c["claim_id"]))
                    await db.commit()
                    stats["indeterminate"] += 1
                    continue
                built = await asyncio.to_thread(ce.cli, "oracle.verdict", {
                    "pubkey": pk, "created_at": min(now, int(mark.get("marked_at") or now)),
                    "claim": c["claim_id"], "experiment": c["experiment"], "outcome": outcome})
                if not built or not isinstance(built.get("event"), dict):
                    continue
                ev = ce.sign(ce.add_tags(built["event"], [["signal", c["record_id"]],
                                                          ["window", c["window"]]]), secret)
                await ce.store(db, ev, claim_id=c["claim_id"], record_id=c["record_id"], source="resolver")
                await db.execute("UPDATE crucible_claims SET verdict_outcome=?, verdict_event_id=?, verdict_at=? "
                                 "WHERE claim_id=?", (outcome, ev["id"], now, c["claim_id"]))
                await db.commit()
                stats["verdicts"] += 1
    except Exception as e:
        logger.warning("crucible verdict pass failed (fail-open): %s", e)
        stats["error"] = str(e)[:200]
    return stats


async def adapter_loop() -> None:
    """Background task (app lifespan). Off = nothing below is entered."""
    while True:
        await asyncio.sleep(INTERVAL_S)
        if not enabled():
            continue
        try:
            a = await process_once()
            v = await verdict_once()
            p = await ce.publish_pending()
            if a.get("crucible") != "ok":
                logger.info("crucible adapter idle: %s", a.get("crucible"))
            elif a["claims"] or a["attestations"] or v["verdicts"] or p["published"]:
                logger.info("crucible adapter: %s %s %s", a, v, p)
        except Exception as e:  # never kill the loop
            logger.warning("crucible adapter cycle failed: %s", e)
