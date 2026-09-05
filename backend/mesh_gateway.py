"""Mesh ingress: LoRa, Meshtastic, Reticulum and DePIN frames reaching Vantage.

`omokoda-mesh` firmware canonicalises every frame it handles -- whatever
network it arrived on -- into one envelope, and a gateway node bridges that
envelope onto Nostr as a signed event. This module is the other end of that
bridge: it verifies such an event, records what it says, and makes the node
that sent it a first-class principal rather than a row in a side table.

The wire contract is `omokoda-mesh/docs/EVENT_KINDS.md`, read directly rather
than from memory:

* kind 20000 mesh packet route, 20001 telemetry, 20003 node presence,
* required tags `network`, `hop`, `origin_id`, plus `reticulum_dest` when the
  origin network is Reticulum,
* one `hop_sig` tag per relaying node, each `[pubkey, signature, timestamp]`,
* and proof-of-relay as kind 1902 -- the ecosystem's attestation schema,
  `stance: confirm`, `e`-tagging the routed packet.

Two limits are recorded honestly rather than papered over, because a gateway
that overstates what it verified is worse than one that verifies nothing.

**Hop signatures are recorded, not verified.** The firmware serialises a hop
trail but nothing in it signs one yet -- there is no hop-signing code in
`lib/`, only the struct and its encoding. So a hop trail here is a claim
about a path, and `hops_verified` is False on every packet until the firmware
signs and this module checks. Scoring must not treat it as proof.

**A bridged packet does not carry its payload.** `envelopeToNostrEvent` puts
the origin pubkey in `content` and drops the payload bytes, by its own
comment. So a mesh packet arriving over the relay is metadata: who relayed
what, when, over which network -- routing and telemetry, not the message. The
payload is retrievable only over the originating transport.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import aiosqlite

from .db import get_db
from .nostr_kinds import kind as kind_number

logger = logging.getLogger(__name__)

KIND_MESH_ROUTE = kind_number("mesh_packet_route")
KIND_MESH_TELEMETRY = kind_number("mesh_telemetry")
KIND_MESH_PRESENCE = kind_number("mesh_node_presence")
KIND_ATTESTATION = kind_number("attestation")

MESH_KINDS = {KIND_MESH_ROUTE, KIND_MESH_TELEMETRY, KIND_MESH_PRESENCE}

#: The origin-network vocabulary, from the firmware's own `SourceNetwork`
#: enum. Closed: an unrecognised network is a firmware version this instance
#: does not understand, and guessing at it would put unlabelled traffic in
#: the same table as labelled traffic.
NETWORKS = {"nostr", "reticulum", "meshtastic", "lorawan", "depin", "local"}

#: A frame cannot legitimately have crossed more relays than the firmware's
#: own hop-trail encoding can carry (one byte of count).
MAX_HOPS = 255


class MeshRejected(ValueError):
    """A mesh event that will not be recorded, and why."""


# ── verification ─────────────────────────────────────────────────────────────

def _canonical_event_id(event: dict) -> str:
    import hashlib

    ser = json.dumps(
        [0, str(event.get("pubkey", "")), int(event.get("created_at") or 0),
         int(event.get("kind") or 0), event.get("tags") or [],
         str(event.get("content") or "")],
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(ser.encode("utf-8")).hexdigest()


def verify_event(event: dict) -> str:
    """Check a mesh event's own signature. Returns the recomputed id.

    The id is recomputed rather than trusted, for the same reason it is in
    the join handshake: an id supplied by the sender proves nothing, so
    verifying a signature over it would prove nothing either.
    """
    if not isinstance(event, dict):
        raise MeshRejected("event must be a JSON object")
    for field in ("id", "pubkey", "sig", "kind", "created_at"):
        if field not in event:
            raise MeshRejected(f"event is missing '{field}'")

    pubkey = str(event["pubkey"])
    if len(pubkey) != 64:
        raise MeshRejected("pubkey must be 64 hex characters (x-only)")

    computed = _canonical_event_id(event)
    if computed != str(event["id"]):
        raise MeshRejected("event id does not match its contents")

    from coincurve import PublicKeyXOnly

    try:
        sig = bytes.fromhex(str(event["sig"]))
        key = PublicKeyXOnly(bytes.fromhex(pubkey))
    except ValueError as exc:
        raise MeshRejected(f"malformed hex: {exc}") from exc
    if len(sig) != 64:
        raise MeshRejected("signature must be 64 bytes")
    try:
        valid = key.verify(sig, bytes.fromhex(computed))
    except Exception as exc:
        raise MeshRejected(f"signature could not be verified: {exc}") from exc
    if not valid:
        raise MeshRejected("signature does not verify for this pubkey")
    return computed


def _tag_values(event: dict, name: str) -> list[list[str]]:
    return [t for t in (event.get("tags") or []) if t and len(t) >= 2 and t[0] == name]


def _first_tag(event: dict, name: str) -> Optional[str]:
    tags = _tag_values(event, name)
    return str(tags[0][1]) if tags else None


def parse_mesh_event(event: dict) -> dict:
    """Pull the firmware's tag contract out of a verified event.

    Refuses rather than defaults on the three required tags. A packet whose
    origin network is unknown is not a packet from an unknown network -- it
    is a packet this instance cannot interpret, and recording it as `local`
    would put it in the same bucket as traffic that really is local.
    """
    kind = int(event.get("kind") or 0)
    if kind not in MESH_KINDS:
        raise MeshRejected(f"kind {kind} is not a mesh kind")

    network = _first_tag(event, "network")
    if network is None:
        raise MeshRejected("mesh events require a 'network' tag")
    if network not in NETWORKS:
        raise MeshRejected(
            f"unknown origin network {network!r}; this instance understands "
            f"{', '.join(sorted(NETWORKS))}"
        )

    hop_raw = _first_tag(event, "hop")
    if hop_raw is None:
        raise MeshRejected("mesh events require a 'hop' tag")
    try:
        hop_count = int(hop_raw)
    except ValueError as exc:
        raise MeshRejected(f"hop must be an integer, got {hop_raw!r}") from exc
    if hop_count < 0 or hop_count > MAX_HOPS:
        raise MeshRejected(f"hop count {hop_count} is outside 0..{MAX_HOPS}")

    origin_id = _first_tag(event, "origin_id")
    if origin_id is None:
        raise MeshRejected("mesh events require an 'origin_id' tag")

    hop_trail = []
    for tag in _tag_values(event, "hop_sig"):
        if len(tag) < 4:
            raise MeshRejected("a hop_sig tag carries [pubkey, signature, timestamp]")
        hop_trail.append({"pubkey": str(tag[1]), "signature": str(tag[2]),
                          "timestamp": str(tag[3])})

    reticulum_dest = _first_tag(event, "reticulum_dest")
    if network == "reticulum" and reticulum_dest is None:
        # The firmware emits it for exactly this network; missing means the
        # event was assembled by something that is not the firmware.
        raise MeshRejected("a reticulum-origin event must carry reticulum_dest")

    return {
        "event_id": str(event["id"]),
        "gateway_pubkey": str(event["pubkey"]),
        "kind": kind,
        "network": network,
        "hop_count": hop_count,
        "origin_id": origin_id,
        "origin_pubkey": str(event.get("content") or ""),
        "reticulum_dest": reticulum_dest,
        "hop_trail": hop_trail,
        # See the module docstring: nothing in the firmware signs a hop yet.
        "hops_verified": False,
        "created_at": int(event.get("created_at") or 0),
    }


# ── schema ───────────────────────────────────────────────────────────────────

async def init_mesh_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mesh_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pubkey TEXT NOT NULL UNIQUE,
                principal_id INTEGER REFERENCES principals(id),
                label TEXT DEFAULT '',
                network TEXT DEFAULT '',
                first_seen_at TEXT DEFAULT (datetime('now')),
                last_seen_at TEXT DEFAULT (datetime('now')),
                packets INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mesh_packets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                kind INTEGER NOT NULL,
                gateway_pubkey TEXT NOT NULL,
                network TEXT NOT NULL,
                origin_id TEXT NOT NULL DEFAULT '',
                origin_pubkey TEXT NOT NULL DEFAULT '',
                reticulum_dest TEXT,
                hop_count INTEGER NOT NULL DEFAULT 0,
                hop_trail TEXT NOT NULL DEFAULT '[]',
                hops_verified INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mesh_packets_network "
            "ON mesh_packets(network, created_at DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mesh_packets_gateway "
            "ON mesh_packets(gateway_pubkey, created_at DESC)"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mesh_relay_attestations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                attestor_pubkey TEXT NOT NULL,
                subject_event_id TEXT NOT NULL,
                stance TEXT NOT NULL DEFAULT 'confirm',
                created_at INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_mesh_attest_subject "
            "ON mesh_relay_attestations(subject_event_id)"
        )
        await db.commit()


# ── the node as a principal ──────────────────────────────────────────────────

async def node_principal(pubkey: str, label: str = "", network: str = "") -> Optional[dict]:
    """Register a gateway node as an external principal, holding its own key.

    Reusing `principals` rather than inventing a node identity table is the
    point: a mesh node is a member that arrived over LoRa instead of over a
    WebSocket, and everything downstream -- membership, presence, scoring --
    should not have to care which.

    Custody is `self` and always will be. The key lives in the node's flash,
    derived from its own master seed; Vantage cannot sign for it and must
    never appear to.
    """
    from . import coordination as coord

    display = label or f"mesh-{pubkey[:8]}"
    try:
        principal = await coord.get_or_create_external_principal(
            pubkey=pubkey, display_name=display, framework="omokoda-mesh",
            capabilities=[network] if network else [],
        )
    except Exception as exc:
        logger.warning("mesh: could not register node %s: %s", pubkey[:8], exc)
        return None

    async with get_db() as db:
        await db.execute(
            """INSERT INTO mesh_nodes (pubkey, principal_id, label, network)
               VALUES (?,?,?,?)
               ON CONFLICT(pubkey) DO UPDATE SET
                 principal_id=excluded.principal_id,
                 last_seen_at=datetime('now'),
                 packets=mesh_nodes.packets + 1""",
            (pubkey, principal["id"], display, network),
        )
        await db.commit()
    return principal


# ── ingress ──────────────────────────────────────────────────────────────────

async def ingest(event: dict, *, register_node: bool = True) -> dict:
    """Verify and record one mesh event. Idempotent on the event id.

    Callable from the relay indexer and from the HTTP endpoint alike, so a
    gateway that can reach the relay and one that can only reach this API
    produce identical rows -- the same stance the coordination indexer takes.
    """
    verify_event(event)
    parsed = parse_mesh_event(event)

    principal = None
    if register_node:
        principal = await node_principal(
            parsed["gateway_pubkey"], network=parsed["network"]
        )

    async with get_db() as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO mesh_packets
                 (event_id, kind, gateway_pubkey, network, origin_id, origin_pubkey,
                  reticulum_dest, hop_count, hop_trail, hops_verified, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (parsed["event_id"], parsed["kind"], parsed["gateway_pubkey"], parsed["network"],
             parsed["origin_id"], parsed["origin_pubkey"], parsed["reticulum_dest"],
             parsed["hop_count"], json.dumps(parsed["hop_trail"]),
             1 if parsed["hops_verified"] else 0, parsed["created_at"]),
        )
        await db.commit()
        new = bool(cur.rowcount)

    return {**parsed, "recorded": new,
            "principal_id": principal["id"] if principal else None}


async def ingest_attestation(event: dict) -> dict:
    """Record a kind 1902 proof-of-relay.

    Verified as far as it can be: the attestation's own signature, and that
    it names a subject. Whether the attestor really forwarded the packet is
    not checkable from here -- that is what the hop trail would prove, once
    the firmware signs one.
    """
    verify_event(event)
    if int(event.get("kind") or 0) != KIND_ATTESTATION:
        raise MeshRejected(f"expected kind {KIND_ATTESTATION}")

    subject = _first_tag(event, "e")
    if not subject:
        raise MeshRejected("an attestation must e-tag the event it confirms")
    stance = _first_tag(event, "stance") or "confirm"
    if stance not in ("vouch", "challenge", "confirm"):
        raise MeshRejected(f"unknown attestation stance {stance!r}")

    async with get_db() as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO mesh_relay_attestations
                 (event_id, attestor_pubkey, subject_event_id, stance, created_at)
               VALUES (?,?,?,?,?)""",
            (str(event["id"]), str(event["pubkey"]), subject, stance,
             int(event.get("created_at") or 0)),
        )
        await db.commit()
        new = bool(cur.rowcount)

    return {"event_id": str(event["id"]), "subject_event_id": subject,
            "stance": stance, "recorded": new}


# ── read side ────────────────────────────────────────────────────────────────

async def recent_packets(limit: int = 100, network: str = "") -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if network:
            cur = await db.execute(
                "SELECT * FROM mesh_packets WHERE network=? ORDER BY id DESC LIMIT ?",
                (network, limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM mesh_packets ORDER BY id DESC LIMIT ?", (limit,)
            )
        rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        row["hop_trail"] = json.loads(row["hop_trail"] or "[]")
        row["hops_verified"] = bool(row["hops_verified"])
    return rows


async def known_nodes(limit: int = 200) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT n.*, p.display_name, p.key_custody
                 FROM mesh_nodes n LEFT JOIN principals p ON p.id = n.principal_id
                ORDER BY n.last_seen_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def attestations_for(event_id: str) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM mesh_relay_attestations WHERE subject_event_id=? ORDER BY id",
            (event_id,),
        )
        return [dict(r) for r in await cur.fetchall()]
