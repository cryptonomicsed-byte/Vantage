"""P3-03 -- daily signed calibration events (Crucible kind 47005).

Once per UTC day, for every source with a Mycelium ``source_hit_rate``
finding (mycelium P2-04, n_eff from P3-02), publish one signed 47005
"scoring-rule update to an agent's reliability" (Crucible SPEC):

  signer   the resolver key (CRUCIBLE_RESOLVER_KEY_FILE) -- the party that
           measured the outcomes. Never a source's own key: a source does not
           grade itself.
  tags     c=<community>, domain=trading, d=<source>:<window>:<YYYY-MM-DD>
           (one per source per window per day; re-runs are idempotent),
           subject=source:<name>, window, p=<source pubkey> when that source
           has a key on the box
  content  {source, window, day, n, hits, n_eff, hits_eff, hit_rate, brier,
            min_samples_met, finding_updated_at}

Nothing is invented: hit_rate stays null below the miner's 30-sample floor,
exactly as the finding says, and a source with no finding gets no event.
Events go to the same outbox as the trading adapter (crucible_events) and
are pushed by publish_pending() when CRUCIBLE_RELAY_URL is set.

Flag CRUCIBLE_CALIBRATION_ENABLED (default off). No crucible CLI is needed:
these events are built and signed in-process (BIP-340), so this works even
before Crucible is installed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from backend import crucible_events as ce

logger = logging.getLogger(__name__)

FLAG = "CRUCIBLE_CALIBRATION_ENABLED"
WINDOWS = ("1h", "6h", "24h")
CHECK_EVERY_S = 3600


def enabled() -> bool:
    return ce.flag(FLAG)


def day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def fetch_findings() -> Optional[List[Dict[str, Any]]]:
    """source_hit_rate finding payloads; None when Mycelium is unreachable."""
    from backend.mycelium_bridge import MYCELIUM_URL
    try:
        with urllib.request.urlopen(f"{MYCELIUM_URL}/api/findings?miner=source_hit_rate&limit=5000",
                                    timeout=10) as r:
            body = json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return None
    out = []
    for f in body.get("findings") or []:
        p = f.get("payload") if isinstance(f, dict) else None
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except ValueError:
                continue
        if isinstance(p, dict) and p.get("source") and p.get("window") in WINDOWS:
            out.append(p)
    return out


def content_for(p: Dict[str, Any], day: str) -> Dict[str, Any]:
    keep = ("n", "hits", "n_eff", "hits_eff", "hit_rate", "brier", "min_samples_met")
    c = {"source": p["source"], "window": p["window"], "day": day}
    c.update({k: p.get(k) for k in keep})
    c["finding_updated_at"] = p.get("updated_at")
    return c


def build_event(p: Dict[str, Any], day: str, secret: bytes, now: int,
                source_pubkey: Optional[str] = None) -> Dict[str, Any]:
    tags = [["c", ce.community()], ["domain", "trading"], ["d", f"{p['source']}:{p['window']}:{day}"],
            ["subject", f"source:{p['source']}"], ["window", p["window"]]]
    if source_pubkey:
        tags.append(["p", source_pubkey])
    unsigned = {"created_at": now, "kind": ce.KIND_CALIBRATION, "tags": tags,
                "content": json.dumps(content_for(p, day), sort_keys=True, separators=(",", ":"))}
    return ce.sign(unsigned, secret)


async def publish_day(now: Optional[float] = None,
                      fetch: Callable[[], Optional[List[Dict[str, Any]]]] = fetch_findings) -> Dict[str, Any]:
    """Sign today's 47005s that are not in the outbox yet. Never raises."""
    stats: Dict[str, Any] = {"events": 0, "already": 0, "status": "ok"}
    secret = ce.resolver_key()
    if secret is None:
        stats["status"] = "no_resolver_key"
        return stats
    now_i = int(now or time.time())
    day = day_of(now_i)
    findings = await asyncio.to_thread(fetch)
    if findings is None:
        stats["status"] = "mycelium_unreachable"
        return stats
    from backend.db import get_db
    try:
        async with get_db() as db:
            await ce.ensure_outbox(db)
            have = {r[0] for r in await (await db.execute(
                "SELECT source FROM crucible_events WHERE kind=? AND record_id LIKE ?",
                (ce.KIND_CALIBRATION, f"calibration:%:{day}"))).fetchall()}
            for p in findings:
                key = f"{p['source']}:{p['window']}"
                if f"calib:{key}" in have:
                    stats["already"] += 1
                    continue
                src_secret = ce.source_key(str(p["source"]))
                ev = build_event(p, day, secret, now_i, ce.pubkey_hex(src_secret) if src_secret else None)
                await ce.store(db, ev, claim_id="", record_id=f"calibration:{key}:{day}",
                               source=f"calib:{key}", attested=True)
                stats["events"] += 1
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("crucible calibration failed (fail-open): %s", e)
        stats["status"] = "error"
        stats["error"] = str(e)[:200]
    return stats


async def calibration_loop() -> None:
    """Hourly check; at most one event per source/window/day (outbox-deduped)."""
    while True:
        await asyncio.sleep(CHECK_EVERY_S)
        if not enabled():
            continue
        try:
            out = await publish_day()
            if out["events"]:
                logger.info("crucible calibration: %s", out)
                await ce.publish_pending()
        except Exception as e:  # noqa: BLE001
            logger.warning("crucible calibration cycle failed: %s", e)
