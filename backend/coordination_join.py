"""The keypair join boundary: how an outside agent framework gets into a guild.

Phase 1 of docs/VANTAGE_SWARM_COORDINATION_SPEC.md. A Claude Code, Hermes or
OpenClaw agent — anything that can hold a secp256k1 key and speak Nostr —
proves control of a pubkey, gets a principal and a guild membership, and is
registered as a relay member. From then on it talks to the relay directly and
never needs this backend again.

The private key never leaves the joining agent. That is the entire point, and
it is why this module only ever *verifies* signatures and never produces one
on an external agent's behalf.

The proof is a NIP-42-shaped kind 22242 event over a server-issued challenge,
which is the same primitive BuzzSession.authenticate already uses against the
relay. Reusing it means a framework that can already authenticate to Buzz can
join a guild with no new crypto code.
"""
import hashlib
import json
import logging
import secrets
import time
from typing import Optional

import aiosqlite
from coincurve import PublicKeyXOnly

from .buzz_registration import RELAY_WS_URL, _docker_exec
from .db import get_db

logger = logging.getLogger(__name__)

KIND_CLIENT_AUTH = 22242

# A challenge is a one-shot credential. Long enough for a human-driven flow
# (paste a challenge into an agent's config, have it sign), short enough that
# a leaked one is not useful for long.
CHALLENGE_TTL_SECONDS = 600

# How far the signed event's own created_at may drift from ours. Guards
# replay of an old signature while tolerating ordinary clock skew.
MAX_CLOCK_SKEW_SECONDS = 300

VALID_FRAMEWORKS_HINT = "claude-code, hermes, openclaw, or any Nostr-capable framework"


class JoinRejected(ValueError):
    """The proof did not check out. The message is safe to return to the
    caller — it says which check failed, because a framework author
    debugging their integration needs to know."""


async def init_join_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS join_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                challenge TEXT NOT NULL UNIQUE,
                guild_id INTEGER NOT NULL REFERENCES guilds(id),
                pubkey TEXT NOT NULL,
                display_name TEXT NOT NULL,
                framework TEXT DEFAULT '',
                capabilities TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now')),
                expires_at INTEGER NOT NULL,
                consumed_at TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_join_challenges_pubkey ON join_challenges(pubkey)")
        await db.commit()


# ── event verification ───────────────────────────────────────────────────────

def _canonical_event_id(event: dict) -> str:
    """NIP-01 serialization: [0, pubkey, created_at, kind, tags, content].

    Mirrors buzz_client._event_id exactly. Recomputing rather than trusting
    the id the client sent is what stops a signature over one payload being
    presented alongside a different one.
    """
    ser = json.dumps(
        [0, event.get("pubkey", ""), int(event.get("created_at") or 0),
         int(event.get("kind") or 0), event.get("tags") or [], event.get("content") or ""],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(ser.encode("utf-8")).hexdigest()


def verify_signed_event(event: dict) -> None:
    """Raise JoinRejected unless the event is internally consistent and the
    BIP-340 signature verifies against its own pubkey."""
    if not isinstance(event, dict):
        raise JoinRejected("signed_event must be a JSON object")

    for field in ("id", "pubkey", "sig", "kind", "created_at"):
        if field not in event:
            raise JoinRejected(f"signed_event is missing '{field}'")

    pubkey = str(event["pubkey"])
    if len(pubkey) != 64:
        raise JoinRejected("pubkey must be 64 hex characters (x-only)")

    computed = _canonical_event_id(event)
    if computed != str(event["id"]):
        raise JoinRejected("event id does not match its contents")

    try:
        sig = bytes.fromhex(str(event["sig"]))
        key = PublicKeyXOnly(bytes.fromhex(pubkey))
        message = bytes.fromhex(computed)
    except ValueError as exc:
        raise JoinRejected(f"malformed hex in signed_event: {exc}") from exc

    if len(sig) != 64:
        raise JoinRejected("signature must be 64 bytes")

    try:
        valid = key.verify(sig, message)
    except Exception as exc:
        raise JoinRejected(f"signature could not be verified: {exc}") from exc
    if not valid:
        raise JoinRejected("signature does not verify for this pubkey")


def _challenge_tag(event: dict) -> Optional[str]:
    for tag in event.get("tags") or []:
        if tag and len(tag) >= 2 and tag[0] == "challenge":
            return str(tag[1])
    return None


# ── the handshake ────────────────────────────────────────────────────────────

async def issue_challenge(
    *, guild_id: int, pubkey: str, display_name: str, framework: str = "",
    capabilities: Optional[list] = None,
) -> dict:
    pubkey = (pubkey or "").strip().lower()
    if len(pubkey) != 64:
        raise JoinRejected("pubkey must be 64 hex characters (x-only, as Nostr uses)")
    try:
        bytes.fromhex(pubkey)
    except ValueError as exc:
        raise JoinRejected("pubkey is not valid hex") from exc

    display_name = (display_name or "").strip()[:80]
    if not display_name:
        raise JoinRejected("display_name is required so people can tell who is speaking")

    challenge = secrets.token_hex(32)
    expires_at = int(time.time()) + CHALLENGE_TTL_SECONDS
    async with get_db() as db:
        await db.execute(
            """INSERT INTO join_challenges
                 (challenge, guild_id, pubkey, display_name, framework, capabilities, expires_at)
               VALUES (?,?,?,?,?,?,?)""",
            (challenge, guild_id, pubkey, display_name, (framework or "")[:40],
             json.dumps(capabilities or []), expires_at),
        )
        await db.commit()

    return {
        "challenge": challenge,
        "expires_at": expires_at,
        "kind": KIND_CLIENT_AUTH,
        "relay": RELAY_WS_URL,
        "instructions": (
            "Sign a kind 22242 event with tags [[\"relay\", <relay url>], "
            "[\"challenge\", <challenge>]] using the key whose pubkey you sent, "
            "then POST it to join-confirm as signed_event."
        ),
    }


async def consume_challenge(challenge: str, guild_id: int) -> dict:
    """Fetch and atomically burn a challenge. Single use, by construction:
    the UPDATE's own WHERE clause is the guard, so two racing confirms
    cannot both win."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM join_challenges WHERE challenge=?", (challenge,))
        row = await cur.fetchone()
    if not row:
        raise JoinRejected("unknown challenge — request a new one")
    row = dict(row)

    if row["guild_id"] != guild_id:
        raise JoinRejected("this challenge was issued for a different guild")
    if row["consumed_at"]:
        raise JoinRejected("this challenge has already been used")
    if int(row["expires_at"]) < int(time.time()):
        raise JoinRejected("challenge expired — request a new one")

    async with get_db() as db:
        cur = await db.execute(
            "UPDATE join_challenges SET consumed_at=datetime('now') WHERE challenge=? AND consumed_at IS NULL",
            (challenge,),
        )
        await db.commit()
        if cur.rowcount != 1:
            raise JoinRejected("this challenge has already been used")
    return row


def check_proof(event: dict, expected_pubkey: str, expected_challenge: str) -> None:
    """Every check the proof has to pass, in the order that gives the most
    useful error first."""
    verify_signed_event(event)

    if int(event.get("kind") or 0) != KIND_CLIENT_AUTH:
        raise JoinRejected(f"signed_event must be kind {KIND_CLIENT_AUTH}")

    if str(event["pubkey"]).lower() != expected_pubkey.lower():
        raise JoinRejected("signed_event pubkey does not match the pubkey in the join request")

    presented = _challenge_tag(event)
    if presented is None:
        raise JoinRejected("signed_event has no [\"challenge\", ...] tag")
    if not secrets.compare_digest(presented, expected_challenge):
        raise JoinRejected("signed_event carries a different challenge")

    drift = abs(int(time.time()) - int(event.get("created_at") or 0))
    if drift > MAX_CLOCK_SKEW_SECONDS:
        raise JoinRejected(
            f"signed_event created_at is {drift}s away from server time "
            f"(max {MAX_CLOCK_SKEW_SECONDS}s) — check the clock and sign again"
        )


async def register_pubkey_on_relay(pubkey: str) -> bool:
    """Grant relay membership to a pubkey Vantage does not hold the key for.

    Same admin path buzz_registration.register_agent_on_buzz and
    buzz_human_identity use — the only mechanism this relay actually offers,
    since a plain member cannot add members itself. Returns False rather than
    raising: a join whose relay registration failed is still a real Vantage
    membership, and the agent can be registered later.
    """
    try:
        code, out, err = await _docker_exec("add-member", "--pubkey", pubkey, "--role", "member")
    except Exception as exc:
        logger.warning("join: relay registration unavailable for %s: %s", pubkey[:8], exc)
        return False
    blob = (out + err).lower()
    if code == 0 or "already" in blob or "exists" in blob:
        return True
    logger.warning("join: buzz-admin add-member failed for %s: %s", pubkey[:8], err.strip() or out.strip())
    return False
