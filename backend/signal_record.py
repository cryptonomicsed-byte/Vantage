"""signal_record/1 -- the one shared record every producer sends (handoff P1-01).

A record travels as optional extra fields on the existing
POST /api/intel/signals/ingest. A payload with no ``schema`` field is a legacy
payload and is handled exactly as before; this module is not entered.

``validate(payload)`` returns ``(record, problems)``. ``record`` is the
normalised record when ``problems`` is empty. The caller (routers/intel.py)
then:

  * on problems  -> 422 + a row in ``signal_rejects``
  * on a repeat id -> the record is ignored (idempotent)
  * otherwise    -> conviction capped from ``source_caps``, mapped onto the
                    legacy pool fields, ingest_signal_internal() unchanged.

Flag: SIGNAL_RECORD_V1 (default off). Off means schema payloads are ingested
as legacy payloads and the extra fields are ignored.

Ground rules this enforces (handoff section 2):
  * conviction cap 0.69 for every source that was not already posting when
    the flag was first turned on (rule 4). Only a human raises a cap, by
    writing a source_caps row; nothing here raises one.
  * an on-chain token address must exist on-chain before a NEW source's
    record is accepted. Unverifiable (no RPC, RPC error) is a rejection,
    not a pass.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Awaitable, Callable, Optional

SCHEMA = "signal_record/1"

# Add chains here, not ad hoc (handoff section 1).
CHAINS = ("solana", "robinhood", "base", "ethereum", "portent", "bitcoin")
EVM_CHAINS = ("robinhood", "base", "ethereum")
DIRECTIONS = ("long", "short", "hold", "exit", "alert")
LEGACY_DIRECTION = {"buy": "long", "bullish": "long", "sell": "short", "bearish": "short"}
PRODUCER_KINDS = ("daemon", "guild_agent", "service")

NEW_SOURCE_CAP = 0.69
EVIDENCE_MAX_BYTES = 8 * 1024
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_SOLANA_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_EVM_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOURCE_REF = re.compile(r"^(tx|x|task|url|portent|pool|ledger):\S+$")
# A created_at more than this far in the future is a clock or unit bug
# (milliseconds sent as seconds), not an event.
_FUTURE_SLACK_S = 300
_EARLIEST = 1_420_070_400  # 2015-01-01; anything older is a unit bug

LEGACY_SNAPSHOT_MARK = "*legacy-snapshot-taken*"


def enabled() -> bool:
    return os.environ.get("SIGNAL_RECORD_V1", "0").strip().lower() in ("1", "true", "yes", "on")


def is_record(payload: Any) -> bool:
    return isinstance(payload, dict) and "schema" in payload


def _str(v: Any) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


def validate(payload: dict, now: Optional[int] = None) -> tuple[dict, list[str]]:
    """Field rules from handoff section 1. Pure: no I/O."""
    now = int(time.time()) if now is None else now
    p: list[str] = []
    rec: dict = {}

    if payload.get("schema") != SCHEMA:
        p.append(f"schema must be exactly {SCHEMA!r}, got {payload.get('schema')!r}")

    rid = _str(payload.get("id"))
    if not rid or not _ULID.match(rid.upper()):
        p.append("id must be a 26-char ULID made by the producer")
    else:
        rec["id"] = rid.upper()

    ca = payload.get("created_at")
    if isinstance(ca, bool) or not isinstance(ca, (int, float)):
        p.append("created_at must be unix seconds of the event (a number)")
    elif ca > now + _FUTURE_SLACK_S:
        p.append(f"created_at {int(ca)} is in the future -- milliseconds sent as seconds?")
    elif ca < _EARLIEST:
        p.append(f"created_at {int(ca)} predates 2015 -- wrong unit?")
    else:
        rec["created_at"] = int(ca)

    src = _str(payload.get("source"))
    if not src:
        p.append("source is required")
    elif len(src) > 30:
        p.append("source must be at most 30 chars")
    else:
        rec["source"] = src

    ref = _str(payload.get("source_ref"))
    if not ref:
        p.append("source_ref is required: a pointer to the evidence (tx:, x:, task:, url:)")
    elif not _SOURCE_REF.match(ref):
        p.append(f"source_ref {ref[:60]!r} must look like tx:<sig>, x:<post id>, task:<id>, url:<page>")
    else:
        rec["source_ref"] = ref

    asset = payload.get("asset")
    if not isinstance(asset, dict):
        p.append("asset must be an object {symbol, chain, address}")
        asset = {}
    sym = _str(asset.get("symbol"))
    if not sym:
        p.append("asset.symbol is required")
    elif len(sym) > 20:
        p.append("asset.symbol must be at most 20 chars")
    chain = (_str(asset.get("chain")) or "").lower()
    if chain not in CHAINS:
        p.append(f"asset.chain must be one of {', '.join(CHAINS)}; got {asset.get('chain')!r}")
    addr = _str(asset.get("address")) or ""
    if chain == "solana" and addr and not _SOLANA_ADDR.match(addr):
        p.append("asset.address is not a base58 Solana mint")
    if chain in EVM_CHAINS and addr and not _EVM_ADDR.match(addr):
        p.append("asset.address is not a 0x EVM contract address")
    # "yes for on-chain tokens": a record without an address can still be a
    # ticker-level call (BTC), but it is never tradeable by mint. Vantage
    # trades by mint, never by ticker (816967d).
    rec["asset"] = {"symbol": sym, "chain": chain, "address": addr}

    typ = _str(payload.get("type"))
    if not typ:
        p.append("type is required")
    elif len(typ) > 20:
        p.append("type must be at most 20 chars")
    else:
        rec["type"] = typ

    d = (_str(payload.get("direction")) or "").lower()
    d = LEGACY_DIRECTION.get(d, d)
    if d not in DIRECTIONS:
        p.append(f"direction must be one of {', '.join(DIRECTIONS)} (buy/sell/bullish/bearish map); got {payload.get('direction')!r}")
    else:
        rec["direction"] = d

    raw = payload.get("conviction")
    try:
        conv = float(raw)
        if conv != conv or conv in (float("inf"), float("-inf")) or not 0.0 <= conv <= 1.0:
            raise ValueError
        rec["conviction"] = conv
    except (TypeError, ValueError):
        p.append(f"conviction must be a number between 0 and 1, got {raw!r}")

    for k, default in (("horizon_hours", 24.0), ("target_pct", None)):
        v = payload.get(k, default)
        if v is None:
            rec[k] = None
            continue
        try:
            fv = float(v)
            if fv != fv or fv in (float("inf"), float("-inf")):
                raise ValueError
            if k == "horizon_hours" and fv <= 0:
                raise ValueError
            rec[k] = fv
        except (TypeError, ValueError):
            p.append(f"{k} must be a finite number" + (" > 0" if k == "horizon_hours" else ""))

    parents = payload.get("parents", [])
    if not isinstance(parents, list) or not all(isinstance(x, str) and x for x in parents):
        p.append("parents must be a list of record ids")
    else:
        rec["parents"] = parents

    prod = payload.get("producer")
    if not isinstance(prod, dict):
        p.append("producer is required: {kind, agent_id, version, lineage, env, blind}")
    else:
        if prod.get("kind") not in PRODUCER_KINDS:
            p.append(f"producer.kind must be one of {', '.join(PRODUCER_KINDS)}")
        if "blind" in prod and not isinstance(prod["blind"], bool):
            p.append("producer.blind must be true or false")
        rec["producer"] = {k: prod.get(k) for k in ("kind", "agent_id", "version", "lineage", "env", "blind")}

    ev = payload.get("evidence", {})
    if not isinstance(ev, dict):
        p.append("evidence must be an object")
    elif len(json.dumps(ev, default=str).encode()) > EVIDENCE_MAX_BYTES:
        p.append("evidence must be at most 8 KB")
    else:
        rec["evidence"] = ev

    tags = payload.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        p.append("tags must be a list of strings")
    else:
        rec["tags"] = tags
    rec["detail"] = str(payload.get("detail") or "")
    rec["schema"] = SCHEMA
    return rec, p


def pool_fields(rec: dict) -> dict:
    """Map a valid record onto today's pool fields. Direction is written in
    the vocabulary the existing ranking already reads (pipeline._BUY_WORDS
    has LONG; SHORT is a sell word), so promotable_signals is untouched."""
    return {
        "symbol": rec["asset"]["symbol"],
        "source": rec["source"],
        "type_": rec["type"],
        "conviction": rec["conviction"],
        "direction": rec["direction"].upper(),
        "detail": rec["detail"],
        "mint": rec["asset"]["address"] if rec["asset"]["chain"] != "portent" else "",
    }


# ── DB pieces (aiosqlite connection passed in) ──────────────────────────────

DDL = (
    """CREATE TABLE IF NOT EXISTS signal_rejects (
         id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT,
         reasons TEXT NOT NULL, payload TEXT NOT NULL, ts INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS source_caps (
         source TEXT PRIMARY KEY, cap REAL, set_by TEXT NOT NULL, set_at TEXT NOT NULL)""",
    # Idempotency on record id. P1-03's signal_ledger is the long-term home;
    # this table is what makes "a repeated id is ignored" hold on its own.
    """CREATE TABLE IF NOT EXISTS signal_record_ids (
         id TEXT PRIMARY KEY, source TEXT NOT NULL, ts INTEGER NOT NULL)""",
)


async def ensure_tables(db) -> None:
    for ddl in DDL:
        await db.execute(ddl)


async def snapshot_legacy_sources(db) -> None:
    """Once, the first time a record arrives with the flag on: every source
    already in the pool keeps cap 1.0 (unchanged behaviour); everything else
    is new and gets 0.69. Frozen, so a new source does not become 'known'
    just by posting once."""
    row = await (await db.execute(
        "SELECT 1 FROM source_caps WHERE source=?", (LEGACY_SNAPSHOT_MARK,))).fetchone()
    if row:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    await db.execute(
        "INSERT OR IGNORE INTO source_caps (source, cap, set_by, set_at) "
        "SELECT DISTINCT source, 1.0, 'legacy-snapshot', ? FROM signal_pool "
        "WHERE source IS NOT NULL AND source != ''", (stamp,))
    await db.execute(
        "INSERT OR IGNORE INTO source_caps (source, cap, set_by, set_at) VALUES (?,?,?,?)",
        (LEGACY_SNAPSHOT_MARK, None, "legacy-snapshot", stamp))


async def cap_for(db, source: str) -> tuple[float, bool]:
    """(cap, is_new_source)."""
    row = await (await db.execute(
        "SELECT cap, set_by FROM source_caps WHERE source=?", (source,))).fetchone()
    if row is None or row[0] is None:
        return NEW_SOURCE_CAP, True
    # A human-set cap (any set_by but the snapshot) does not make a source
    # "established": it still needs verified addresses.
    return float(row[0]), row[1] != "legacy-snapshot"


async def claim_id(db, rid: str, source: str) -> bool:
    """True if this id is new (and now claimed); False if it was seen before."""
    cur = await db.execute(
        "INSERT OR IGNORE INTO signal_record_ids (id, source, ts) VALUES (?,?,?)",
        (rid, source, int(time.time())))
    return cur.rowcount == 1


async def reject(db, payload: Any, problems: list[str]) -> None:
    src = payload.get("source") if isinstance(payload, dict) else None
    await db.execute(
        "INSERT INTO signal_rejects (source, reasons, payload, ts) VALUES (?,?,?,?)",
        (str(src)[:64] if src else None, json.dumps(problems),
         json.dumps(payload, default=str)[:65536], int(time.time())))


# ── On-chain existence (new sources only) ───────────────────────────────────

AddressChecker = Callable[[str, str], Awaitable[Optional[bool]]]
_verified: dict[tuple[str, str], float] = {}
_VERIFIED_TTL = 6 * 3600


async def rpc_address_exists(chain: str, address: str) -> Optional[bool]:
    """True/False from a free RPC; None when it cannot be checked."""
    import httpx
    if chain == "solana":
        url = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        body = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                "params": [address, {"encoding": "base64", "commitment": "confirmed"}]}
        pick = lambda r: (r.get("result") or {}).get("value") is not None  # noqa: E731
    elif chain in EVM_CHAINS:
        url = os.environ.get(f"{chain.upper()}_RPC_URL", "")
        if not url:
            return None
        body = {"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]}
        pick = lambda r: (r.get("result") or "0x") not in ("0x", "0x0")  # noqa: E731
    else:
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = (await c.post(url, json=body)).json()
        if "error" in r:
            return None
        return bool(pick(r))
    except Exception:
        return None


async def address_problem(rec: dict, is_new_source: bool,
                          checker: Optional[AddressChecker] = None) -> Optional[str]:
    chain, addr = rec["asset"]["chain"], rec["asset"]["address"]
    if not is_new_source or chain == "portent":
        return None
    if not addr:
        # A ticker-level call (BTC, ETH, SOL) has no token address. It is
        # accepted, and it can never be traded by mint (Vantage trades by
        # mint, never by ticker -- 816967d). Only an address is verified.
        return None
    key = (chain, addr)
    if time.time() - _verified.get(key, 0) < _VERIFIED_TTL:
        return None
    ok = await (checker or rpc_address_exists)(chain, addr)
    if ok is True:
        _verified[key] = time.time()
        return None
    if ok is False:
        return f"asset.address {addr} does not exist on {chain}"
    return f"asset.address {addr} could not be verified on {chain} (RPC unavailable); a new source's record needs a verified address"


# Tests replace this; production uses the RPC.
address_checker: Optional[AddressChecker] = None
