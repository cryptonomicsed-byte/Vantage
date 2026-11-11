"""Who may read this instance's federation surface.

Federation is off unless the operator turns it on (VANTAGE_FEDERATION_ENABLED).
When it is on, a read is allowed for exactly two kinds of caller:

  * a local agent (valid X-Agent-Key), and
  * an ADMITTED peer instance proving it holds its pinned Nostr key: it signs
    sha256("<METHOD> <path> <unix-ts>") with BIP-340 and sends
    X-Peer-Pubkey / X-Peer-Ts / X-Peer-Sig. The pubkey must be the one pinned
    on a peer row an operator admitted, and the timestamp within 5 minutes.

Everyone else gets 401. Admission is a Sentinel operator decision; peers
learned through gossip start un-admitted and are never polled until admitted.
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional

from fastapi import Header, HTTPException, Request

from .config import settings
from .db import get_db

MAX_SKEW_S = 300


def request_digest(method: str, path: str, ts: str) -> str:
    return hashlib.sha256(f"{method.upper()} {path} {ts}".encode()).hexdigest()


async def signed_headers(method: str, path: str) -> dict:
    """Headers this instance sends when it calls a peer's federation route."""
    from .buzz_identity import derive_instance_keypair, public_key_xonly_hex, sign_event_id

    pk = await derive_instance_keypair()
    ts = str(int(time.time()))
    return {"X-Peer-Pubkey": public_key_xonly_hex(pk), "X-Peer-Ts": ts,
            "X-Peer-Sig": sign_event_id(pk, request_digest(method, path, ts))}


def _verify(pubkey_hex: str, digest_hex: str, sig_hex: str) -> bool:
    try:
        from coincurve import PublicKeyXOnly

        return PublicKeyXOnly(bytes.fromhex(pubkey_hex)).verify(bytes.fromhex(sig_hex), bytes.fromhex(digest_hex))
    except Exception:
        return False


async def _local_agent(x_agent_key: Optional[str]) -> bool:
    if not x_agent_key:
        return False
    hashed = hashlib.sha256(x_agent_key.encode()).hexdigest()
    async with get_db() as db:
        row = await (await db.execute("SELECT id FROM agents WHERE api_key=?", (hashed,))).fetchone()
    return row is not None


async def require_federation_reader(
    request: Request,
    x_agent_key: Optional[str] = Header(None),
    x_peer_pubkey: Optional[str] = Header(None),
    x_peer_ts: Optional[str] = Header(None),
    x_peer_sig: Optional[str] = Header(None),
) -> dict:
    if not settings.FEDERATION_ENABLED:
        raise HTTPException(404, "Federation is not enabled on this instance")
    if await _local_agent(x_agent_key):
        return {"kind": "agent"}
    if not (x_peer_pubkey and x_peer_ts and x_peer_sig):
        raise HTTPException(401, "Federation reads need a local agent key or a signed, admitted peer")
    try:
        skew = abs(int(time.time()) - int(x_peer_ts))
    except ValueError:
        raise HTTPException(401, "Bad X-Peer-Ts")
    if skew > MAX_SKEW_S:
        raise HTTPException(401, "Peer signature expired")
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM federation_peers WHERE nostr_pubkey=? AND COALESCE(admitted,0)=1 AND COALESCE(flagged,0)=0",
            (x_peer_pubkey.lower(),),
        )).fetchone()
    if not row:
        raise HTTPException(401, "Unknown or un-admitted peer")
    if not _verify(x_peer_pubkey, request_digest(request.method, request.url.path, x_peer_ts), x_peer_sig):
        raise HTTPException(401, "Peer signature does not verify")
    return {"kind": "peer", "peer_id": row[0]}
