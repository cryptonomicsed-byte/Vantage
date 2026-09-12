"""DIP (Decentralized Interoperability Protocol) envelope ingest and listing.

Sovereign-node sends envelopes here; this router stores them, lets peers
list them, and provides per-envelope acknowledgement so the sending node
knows delivery was registered.

DipEnvelope wire format (matches sovereign-stack dip crate):
    {
        "version":     "dip/1",
        "message_id":  "<uuid>",
        "timestamp":   <ms epoch>,
        "ttl":         <seconds>,
        "origin":      { "network": "vantage", "address": "<did>", "did": "<did>" },
        "destination": { "network": "vantage", "address": "<did>", "did": "<did>" },
        "routing":     [],            // Vec<DipHop> — NOT "hops", NOT "hop_count"
        "kind":        "message",
        "payload":     { ... },
        "identity":    { "principal_id": "...", "agent_id": "...",
                         "session_id": "...", "execution_id": "...", "receipt_id": "..." },
        "merkle_root": "<64-char hex>",
        "signature":   "<sig>"
    }

DipAddress note: `did` is Option<String> in Rust — it is null for Nostr/Meshtastic
addresses. Validation accepts null; only `network` and `address` are required.

DipHop shape: { "node": DipAddress, "adapter": "<network>", "timestamp": <ms>, "latency_ms": <int|null> }
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..db import get_db
from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dip", tags=["dip"])

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

# Top-level fields required on every inbound DipEnvelope.
_REQUIRED_FIELDS = (
    "version", "message_id", "timestamp", "ttl",
    "origin", "destination", "routing",
    "kind", "payload", "identity", "merkle_root", "signature",
)

# Required sub-fields in the identity chain.
_IDENTITY_FIELDS = ("principal_id", "agent_id", "session_id", "execution_id", "receipt_id")

# Valid DipKind variants (snake_case, matches Rust serde rename_all)
_VALID_KINDS = frozenset({"capability", "message", "evidence", "receipt", "event", "claim"})

# Valid DipNetwork variants
_VALID_NETWORKS = frozenset({"vantage", "nostr", "a2a", "mcp", "meshtastic", "freenet", "libp2p"})

# P0-8: SSRF guard — routing hop addresses must not resolve to private/internal IPs.
_PRIVATE_ADDR_RE = re.compile(
    r'(^|\b)(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1|localhost\b)',
    re.IGNORECASE,
)

def _assert_safe_routing(routing: list) -> None:
    for hop in routing:
        node = hop.get("node", {}) if isinstance(hop, dict) else {}
        addr = str(node.get("address", ""))
        if addr and _PRIVATE_ADDR_RE.search(addr):
            raise HTTPException(400, f"Routing hop targets private/internal address: {addr!r}")


async def _ensure_table() -> None:
    """Create or migrate dip_envelopes to the canonical schema."""
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS dip_envelopes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id     TEXT UNIQUE NOT NULL,
                version        TEXT NOT NULL DEFAULT 'dip/1',
                origin_did     TEXT,
                origin_network TEXT NOT NULL,
                origin_address TEXT NOT NULL,
                dest_did       TEXT,
                dest_network   TEXT NOT NULL,
                dest_address   TEXT NOT NULL,
                routing_json   TEXT NOT NULL DEFAULT '[]',
                kind           TEXT NOT NULL,
                payload        TEXT NOT NULL,
                identity       TEXT NOT NULL,
                merkle_root    TEXT NOT NULL,
                signature      TEXT NOT NULL,
                ttl            INTEGER,
                env_timestamp  INTEGER,
                received_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                acknowledged_at TIMESTAMP
            )
        """)
        # Migrate old schema (routing_hops int → routing_json text) if needed
        await _migrate_routing_column(db)

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dip_env_origin_did "
            "ON dip_envelopes(origin_did)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dip_env_dest_did "
            "ON dip_envelopes(dest_did)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dip_env_kind "
            "ON dip_envelopes(kind)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dip_env_ack "
            "ON dip_envelopes(dest_did, acknowledged_at)"
        )
        await db.commit()


async def _migrate_routing_column(db) -> None:
    """Add new columns absent from older schema versions (idempotent)."""
    async with db.execute("PRAGMA table_info(dip_envelopes)") as cur:
        cols = {row[1] async for row in cur}
    for col, defn in [
        ("routing_json",   "TEXT NOT NULL DEFAULT '[]'"),
        ("origin_address", "TEXT NOT NULL DEFAULT ''"),
        ("dest_address",   "TEXT NOT NULL DEFAULT ''"),
    ]:
        if col not in cols:
            try:
                await db.execute(f"ALTER TABLE dip_envelopes ADD COLUMN {col} {defn}")
            except Exception:
                pass


import asyncio as _asyncio


def _schedule_init() -> None:
    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_ensure_table())
    except RuntimeError:
        pass


_schedule_init()


# ── validation ────────────────────────────────────────────────────────────────


def _validate_envelope(body: dict) -> None:
    """Raise HTTPException(422) if body does not match DipEnvelope shape."""
    missing = [f for f in _REQUIRED_FIELDS if f not in body]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error": f"Missing required DipEnvelope fields: {', '.join(missing)}"},
        )

    if body["version"] != "dip/1":
        raise HTTPException(
            status_code=422,
            detail={"error": f"Unsupported DIP version: {body['version']!r} (expected 'dip/1')"},
        )

    for peer_field in ("origin", "destination"):
        peer = body[peer_field]
        if not isinstance(peer, dict):
            raise HTTPException(
                status_code=422,
                detail={"error": f"'{peer_field}' must be a DipAddress object"},
            )
        # `network` and `address` are always required; `did` is Option<String> → may be null
        if not peer.get("network"):
            raise HTTPException(
                status_code=422,
                detail={"error": f"'{peer_field}.network' is required"},
            )
        if peer["network"] not in _VALID_NETWORKS:
            raise HTTPException(
                status_code=422,
                detail={"error": f"'{peer_field}.network' must be one of {sorted(_VALID_NETWORKS)}"},
            )
        if not peer.get("address"):
            raise HTTPException(
                status_code=422,
                detail={"error": f"'{peer_field}.address' is required"},
            )
        # `did` intentionally not required — null is valid for Nostr/Meshtastic addresses

    if not isinstance(body["routing"], list):
        raise HTTPException(
            status_code=422,
            detail={"error": "'routing' must be a list (Vec<DipHop>) — not 'hops' or 'hop_count'"},
        )
    # P0-8: guard against SSRF via routing hops pointing at internal network
    _assert_safe_routing(body["routing"])

    kind = str(body.get("kind", "")).lower()
    if kind not in _VALID_KINDS:
        raise HTTPException(
            status_code=422,
            detail={"error": f"'kind' must be one of {sorted(_VALID_KINDS)}, got {kind!r}"},
        )

    identity = body["identity"]
    if not isinstance(identity, dict):
        raise HTTPException(status_code=422, detail={"error": "'identity' must be an object"})
    missing_id = [f for f in _IDENTITY_FIELDS if not identity.get(f)]
    if missing_id:
        raise HTTPException(
            status_code=422,
            detail={"error": f"Missing identity chain fields: {', '.join(missing_id)}"},
        )

    merkle_root = body.get("merkle_root", "")
    if not _HEX64.match(str(merkle_root)):
        raise HTTPException(
            status_code=422,
            detail={"error": "merkle_root must be a 64-character hex string"},
        )

    message_id = str(body.get("message_id", "")).strip()
    if not message_id:
        raise HTTPException(status_code=422, detail={"error": "message_id must not be empty"})


# ── helpers ───────────────────────────────────────────────────────────────────


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    for field in ("payload", "identity", "routing_json"):
        try:
            d[field] = json.loads(d[field])
        except (TypeError, ValueError, KeyError):
            pass
    # Expose routing under the canonical name
    if "routing_json" in d:
        d["routing"] = d.pop("routing_json")
    return d


def _opt_str(value) -> Optional[str]:
    """Return None if value is None/null; otherwise str(value)."""
    return None if value is None else str(value)


async def _fetch_envelope(message_id: str) -> dict:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM dip_envelopes WHERE message_id=?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Envelope not found")
    return _row_to_dict(row)


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.post("/inbound", summary="Accept a DIP envelope")
async def dip_inbound(request: Request, agent: dict = Depends(get_agent)):
    """Accept a DipEnvelope from a sovereign-node or peer.

    The envelope must match the canonical DipEnvelope shape from the
    sovereign-stack dip crate. See module docstring for the full structure.
    """
    await _ensure_table()

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail={"error": "Request body must be valid JSON"})

    _validate_envelope(body)

    message_id  = str(body["message_id"]).strip()
    origin      = body["origin"]
    destination = body["destination"]
    identity    = body["identity"]

    payload     = body["payload"]
    payload_str = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
    identity_str = json.dumps(identity)
    routing_str  = json.dumps(body.get("routing", []))

    ttl = body.get("ttl")
    try:
        ttl = int(ttl)
    except (TypeError, ValueError):
        ttl = None

    env_ts = body.get("timestamp")
    try:
        env_ts = int(env_ts)
    except (TypeError, ValueError):
        env_ts = None

    accepted_at = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO dip_envelopes
                     (message_id, version,
                      origin_did, origin_network, origin_address,
                      dest_did,   dest_network,   dest_address,
                      routing_json, kind, payload, identity,
                      merkle_root, signature, ttl, env_timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    message_id,
                    str(body["version"]),
                    _opt_str(origin.get("did")),       # null for Nostr/Meshtastic
                    str(origin["network"]),
                    str(origin["address"]),
                    _opt_str(destination.get("did")),  # null for Nostr/Meshtastic
                    str(destination["network"]),
                    str(destination["address"]),
                    routing_str,
                    str(body["kind"]).lower(),
                    payload_str,
                    identity_str,
                    str(body["merkle_root"]),
                    str(body["signature"]),
                    ttl,
                    env_ts,
                ),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            logger.debug("dip_ingest: duplicate message_id %s ignored", message_id)

    return {"status": "accepted", "message_id": message_id, "accepted_at": accepted_at}


@router.get("/outbound", summary="Poll DIP envelopes queued for a node (NAT traversal)")
async def dip_outbound(
    agent: dict = Depends(get_agent),
    did: str = Query(..., description="The node's DID to drain queued envelopes for"),
    limit: int = Query(50, ge=1, le=200),
):
    """Return unacknowledged envelopes addressed to `did` and mark them acknowledged.

    Sovereign-nodes behind NAT call this endpoint to drain their inbound queue.
    Vantage queues any envelope whose `destination.did` matches the caller's DID.
    Envelopes are marked acknowledged atomically so they are not returned twice.
    """
    await _ensure_table()

    now = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM dip_envelopes
               WHERE dest_did=? AND acknowledged_at IS NULL
               ORDER BY received_at ASC
               LIMIT ?""",
            (did, limit),
        ) as cur:
            rows = await cur.fetchall()

        if rows:
            ids = tuple(r["message_id"] for r in rows)
            placeholders = ",".join("?" * len(ids))
            await db.execute(
                f"UPDATE dip_envelopes SET acknowledged_at=? WHERE message_id IN ({placeholders})",
                (now, *ids),
            )
            await db.commit()

    envelopes = [_row_to_dict(r) for r in rows]
    logger.debug("dip_outbound: drained %d envelope(s) for %s", len(envelopes), did)
    return envelopes


@router.get("/envelopes", summary="List received DIP envelopes")
async def list_envelopes(
    agent: dict = Depends(get_agent),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    origin_did: str | None = Query(None, description="Filter by origin DID"),
    kind: str | None = Query(None, description="Filter by envelope kind"),
):
    """List envelopes ordered by received_at descending."""
    clauses: list[str] = []
    params: list = []

    if origin_did:
        clauses.append("origin_did=?")
        params.append(origin_did)
    if kind:
        clauses.append("kind=?")
        params.append(kind.lower())

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT COUNT(*) FROM dip_envelopes {where}", params) as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        async with db.execute(
            f"SELECT * FROM dip_envelopes {where} ORDER BY received_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ) as cur:
            rows = await cur.fetchall()

    return {"envelopes": [_row_to_dict(r) for r in rows], "total": total}


@router.get("/envelopes/{message_id}", summary="Get a single DIP envelope")
async def get_envelope(message_id: str, agent: dict = Depends(get_agent)):
    """Retrieve one envelope by its message_id."""
    return await _fetch_envelope(message_id)


@router.post("/ack/{message_id}", summary="Acknowledge a DIP envelope")
async def ack_envelope(message_id: str, agent: dict = Depends(get_agent)):
    """Mark an envelope as acknowledged (idempotent)."""
    await _fetch_envelope(message_id)

    acked_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            """UPDATE dip_envelopes
               SET acknowledged_at=?
               WHERE message_id=? AND acknowledged_at IS NULL""",
            (acked_at, message_id),
        )
        await db.commit()

    return await _fetch_envelope(message_id)
