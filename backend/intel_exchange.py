"""Opt-in intel exchange between sovereign instances.

Federation already carries published content (`/federation/feed`) and the
knowledge graph (`/federation/ask`). It deliberately does **not** carry
trading signals, which is the right default: your alpha should not leak
merely because you federated. But it left no way to share signals with a peer
even when both sides want to. This is that way.

Two instances that trust each other each configure one half:

  * the **exporter** decides what it is willing to give a named peer —
    which sources, which types, above what conviction;
  * the **importer** decides what it is willing to take from that peer, and
    at what trust tier.

Both halves must exist for anything to move. Neither side can start a flow
unilaterally, and either side ends it by revoking its own half.

## The safety property this module exists to protect

`routers/trading.py` auto-creates a **real order** for any signal with
conviction above 0.7. A naive "import peer signals into the signal pool"
feature would therefore hand every peer a remote trading trigger on your
account — one wrong or malicious peer, and real money moves.

So imported signals are quarantined by construction:

  * they land in `imported_signals`, never in `signal_pool`;
  * conviction is clamped below the auto-execution threshold on the way in,
    so a promoted signal cannot clear it even by accident;
  * and **there is no trust tier that auto-executes.** Turning a peer's
    signal into an order is always a local, explicit act by the operator.

That last point is not a default to be relaxed later — it is the design. A
peer can inform your decisions; it cannot make them.
"""
import json
import logging
import time
from typing import Optional

import aiosqlite

from .db import get_db

logger = logging.getLogger(__name__)

DIRECTIONS = {"export", "import"}
STATUSES = {"active", "paused", "revoked"}

# Trust tiers for imported signals. Neither executes -- see the module
# docstring. The tier only decides how visible an imported signal is.
TIER_ADVISORY = "advisory"   # quarantined; visible only in the imported view
TIER_POOLED = "pooled"       # also surfaced in the local signal feed, attributed
TRUST_TIERS = {TIER_ADVISORY, TIER_POOLED}

# trading.py auto-creates a real order above 0.7. Imported conviction is
# clamped below that so a peer's number can never, by any path, clear the
# execution threshold on this instance.
AUTO_EXECUTION_THRESHOLD = 0.7
IMPORT_CONVICTION_CEILING = 0.69

CHALLENGE_TTL_SECONDS = 300
MAX_BATCH = 500


class ExchangeRefused(ValueError):
    """A peer asked for something no agreement entitles it to."""


async def init_intel_exchange_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS intel_exchange_agreements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id INTEGER NOT NULL REFERENCES federation_peers(id),
                direction TEXT NOT NULL,              -- export | import
                status TEXT NOT NULL DEFAULT 'active',
                sources TEXT NOT NULL DEFAULT '[]',   -- JSON array; [] means any
                signal_types TEXT NOT NULL DEFAULT '[]',
                min_conviction REAL NOT NULL DEFAULT 0.0,
                trust_tier TEXT NOT NULL DEFAULT 'advisory',  -- import side only
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(peer_id, direction)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_exchange_agreements_peer "
            "ON intel_exchange_agreements(peer_id, direction)"
        )

        # Imported signals live here and nowhere else. Keeping them out of
        # signal_pool is what stops them reaching the execution path.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS imported_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_id INTEGER NOT NULL REFERENCES federation_peers(id),
                peer_name TEXT DEFAULT '',
                remote_id TEXT NOT NULL,
                symbol TEXT, source TEXT, signal_type TEXT,
                conviction REAL, direction TEXT, detail TEXT, mint TEXT DEFAULT '',
                remote_ts INTEGER,
                trust_tier TEXT NOT NULL DEFAULT 'advisory',
                promoted_at TEXT DEFAULT NULL,
                imported_at TEXT DEFAULT (datetime('now')),
                UNIQUE(peer_id, remote_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_imported_signals_recent "
            "ON imported_signals(imported_at DESC)"
        )

        await db.execute("""
            CREATE TABLE IF NOT EXISTS exchange_challenges (
                challenge TEXT PRIMARY KEY,
                peer_pubkey TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed_at TEXT
            )
        """)
        await db.commit()


# ── agreements ───────────────────────────────────────────────────────────────

def _json_list(value, field: str) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ExchangeRefused(f"{field} must be a JSON array: {exc}") from exc
    if not isinstance(value, list):
        raise ExchangeRefused(f"{field} must be a JSON array")
    return json.dumps([str(v)[:40] for v in value[:50]])


async def set_agreement(
    *, peer_id: int, direction: str, sources=None, signal_types=None,
    min_conviction: float = 0.0, trust_tier: str = TIER_ADVISORY,
    status: str = "active", note: str = "",
) -> dict:
    if direction not in DIRECTIONS:
        raise ExchangeRefused("direction must be 'export' or 'import'")
    if status not in STATUSES:
        raise ExchangeRefused(f"status must be one of {sorted(STATUSES)}")
    if trust_tier not in TRUST_TIERS:
        raise ExchangeRefused(f"trust_tier must be one of {sorted(TRUST_TIERS)}")
    try:
        min_conviction = float(min_conviction)
    except (TypeError, ValueError) as exc:
        raise ExchangeRefused("min_conviction must be a number between 0 and 1") from exc
    if not 0.0 <= min_conviction <= 1.0:
        raise ExchangeRefused("min_conviction must be between 0 and 1")

    async with get_db() as db:
        cur = await db.execute("SELECT id, name FROM federation_peers WHERE id=?", (peer_id,))
        peer = await cur.fetchone()
        if not peer:
            raise ExchangeRefused("no such federation peer")

        await db.execute(
            """INSERT INTO intel_exchange_agreements
                 (peer_id, direction, status, sources, signal_types, min_conviction,
                  trust_tier, note)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(peer_id, direction) DO UPDATE SET
                 status=excluded.status, sources=excluded.sources,
                 signal_types=excluded.signal_types, min_conviction=excluded.min_conviction,
                 trust_tier=excluded.trust_tier, note=excluded.note,
                 updated_at=datetime('now')""",
            (peer_id, direction, status, _json_list(sources, "sources"),
             _json_list(signal_types, "signal_types"), min_conviction, trust_tier, note[:200]),
        )
        await db.commit()
    return await get_agreement(peer_id, direction)


async def get_agreement(peer_id: int, direction: str) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM intel_exchange_agreements WHERE peer_id=? AND direction=?",
            (peer_id, direction),
        )
        row = await cur.fetchone()
    if not row:
        return None
    agreement = dict(row)
    agreement["sources"] = json.loads(agreement["sources"] or "[]")
    agreement["signal_types"] = json.loads(agreement["signal_types"] or "[]")
    return agreement


async def list_agreements() -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT a.*, p.name AS peer_name, p.url AS peer_url
                 FROM intel_exchange_agreements a
                 JOIN federation_peers p ON p.id = a.peer_id
                ORDER BY a.direction, p.name"""
        )
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["sources"] = json.loads(row["sources"] or "[]")
        row["signal_types"] = json.loads(row["signal_types"] or "[]")
    return rows


# ── the export side ──────────────────────────────────────────────────────────

async def signals_for_peer(peer_id: int, *, since: int = 0, limit: int = 100) -> list[dict]:
    """What this instance is willing to give one peer.

    Filtering happens here rather than at the caller, so a peer cannot widen
    its own entitlement by asking differently.
    """
    agreement = await get_agreement(peer_id, "export")
    if not agreement or agreement["status"] != "active":
        raise ExchangeRefused("no active export agreement for this peer")

    limit = max(1, min(int(limit), MAX_BATCH))
    clauses = ["ts > ?", "conviction >= ?"]
    params: list = [int(since), agreement["min_conviction"]]

    if agreement["sources"]:
        clauses.append(f"source IN ({','.join('?' for _ in agreement['sources'])})")
        params += agreement["sources"]
    if agreement["signal_types"]:
        clauses.append(f"type IN ({','.join('?' for _ in agreement['signal_types'])})")
        params += agreement["signal_types"]
    params.append(limit)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""SELECT id, symbol, source, type, conviction, direction, detail, mint, ts
                  FROM signal_pool
                 WHERE {' AND '.join(clauses)}
                 ORDER BY ts ASC LIMIT ?""",
            tuple(params),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    return [
        {
            "remote_id": str(r["id"]),
            "symbol": r["symbol"], "source": r["source"], "type": r["type"],
            "conviction": r["conviction"], "direction": r["direction"],
            "detail": r["detail"], "mint": r["mint"], "ts": r["ts"],
        }
        for r in rows
    ]


# ── the import side ──────────────────────────────────────────────────────────

def clamp_imported_conviction(value) -> float:
    """Hold an imported signal below the auto-execution threshold.

    A peer's conviction is its opinion, not an instruction. Clamping here
    means no arithmetic further down the line — a promotion, a re-score, a
    future feature that forwards it somewhere — can produce a number that
    trips the 0.7 auto-order rule.
    """
    try:
        conviction = float(value)
    except (TypeError, ValueError):
        return 0.0
    if conviction != conviction:  # NaN
        return 0.0
    return max(0.0, min(conviction, IMPORT_CONVICTION_CEILING))


async def import_signals(peer_id: int, signals: list[dict]) -> dict:
    """Take a batch from a peer, subject to this instance's import agreement.

    Returns counts rather than raising on individual rejects: a peer sending
    one signal outside the agreement should not cost the whole batch.
    """
    agreement = await get_agreement(peer_id, "import")
    if not agreement or agreement["status"] != "active":
        raise ExchangeRefused("no active import agreement for this peer")

    async with get_db() as db:
        cur = await db.execute("SELECT name FROM federation_peers WHERE id=?", (peer_id,))
        row = await cur.fetchone()
    peer_name = (row[0] if row else "") or f"peer-{peer_id}"

    accepted = 0
    filtered = 0
    for signal in signals[:MAX_BATCH]:
        source = str(signal.get("source") or "")[:30]
        signal_type = str(signal.get("type") or signal.get("signal_type") or "")[:20]
        conviction_raw = signal.get("conviction", 0)

        if agreement["sources"] and source not in agreement["sources"]:
            filtered += 1
            continue
        if agreement["signal_types"] and signal_type not in agreement["signal_types"]:
            filtered += 1
            continue
        try:
            if float(conviction_raw) < agreement["min_conviction"]:
                filtered += 1
                continue
        except (TypeError, ValueError):
            filtered += 1
            continue

        remote_id = str(signal.get("remote_id") or signal.get("id") or "")[:64]
        if not remote_id:
            filtered += 1
            continue

        async with get_db() as db:
            cur = await db.execute(
                """INSERT OR IGNORE INTO imported_signals
                     (peer_id, peer_name, remote_id, symbol, source, signal_type,
                      conviction, direction, detail, mint, remote_ts, trust_tier)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (peer_id, peer_name, remote_id,
                 str(signal.get("symbol") or "")[:20], source, signal_type,
                 clamp_imported_conviction(conviction_raw),
                 str(signal.get("direction") or "")[:10],
                 str(signal.get("detail") or "")[:2000],
                 str(signal.get("mint") or "")[:64],
                 int(signal.get("ts") or time.time()),
                 agreement["trust_tier"]),
            )
            await db.commit()
            if cur.rowcount:
                accepted += 1

    logger.info("intel exchange: imported %d from peer %s (%d filtered)",
                accepted, peer_name, filtered)
    return {
        "peer_id": peer_id, "peer_name": peer_name,
        "accepted": accepted, "filtered": filtered,
        "trust_tier": agreement["trust_tier"],
        "quarantined": True,
        "note": (
            "Imported signals are advisory. They are not in the local signal pool "
            "and cannot trigger an order — promoting one is a local decision."
        ),
    }


async def list_imported(
    *, limit: int = 100, peer_id: Optional[int] = None, tier: Optional[str] = None
) -> list[dict]:
    clauses = ["1=1"]
    params: list = []
    if peer_id is not None:
        clauses.append("peer_id = ?")
        params.append(peer_id)
    if tier:
        clauses.append("trust_tier = ?")
        params.append(tier)
    params.append(max(1, min(int(limit), MAX_BATCH)))

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""SELECT * FROM imported_signals WHERE {' AND '.join(clauses)}
                 ORDER BY imported_at DESC, id DESC LIMIT ?""",
            tuple(params),
        )
        return [dict(r) for r in await cur.fetchall()]


async def promote_imported(imported_id: int) -> dict:
    """Surface one imported signal in the local feed, attributed to its peer.

    Deliberately per-signal and operator-driven. The promoted copy keeps the
    clamped conviction and a source string naming the peer, so nothing
    downstream can mistake a peer's opinion for this instance's own work.

    Note what this does *not* do: it does not post to the trading ingest
    endpoint. There is no code path from an imported signal to an order.
    """
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM imported_signals WHERE id=?", (imported_id,))
        row = await cur.fetchone()
    if not row:
        raise ExchangeRefused("no such imported signal")
    row = dict(row)
    if row["promoted_at"]:
        return {"promoted": False, "reason": "already promoted", "id": imported_id}

    conviction = clamp_imported_conviction(row["conviction"])
    async with get_db() as db:
        await db.execute(
            """INSERT INTO signal_pool (symbol, source, type, conviction, direction, detail, mint, ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (row["symbol"], f"peer:{row['peer_name']}"[:30], row["signal_type"],
             conviction, row["direction"],
             f"[via {row['peer_name']}] {row['detail'] or ''}"[:2000],
             row["mint"], int(time.time())),
        )
        await db.execute(
            "UPDATE imported_signals SET promoted_at=datetime('now') WHERE id=?", (imported_id,)
        )
        await db.commit()

    return {
        "promoted": True, "id": imported_id, "conviction": conviction,
        "source": f"peer:{row['peer_name']}",
        "note": "Surfaced in the local feed, attributed. Still below the auto-execution threshold.",
    }
