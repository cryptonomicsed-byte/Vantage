"""Intel exchange: deliberate, consent-based signal sharing between instances.

Three audiences, three auth models:

  * **This instance's operator** configures agreements and promotes imported
    signals. Admin key.
  * **A peer instance** pulls what it is entitled to from `/export`. It
    authenticates by signing a challenge with the pubkey this instance pinned
    for it in `federation_peers.nostr_pubkey` — the same TOFU trust anchor
    federation already uses, not a shared secret.
  * **Agents** read the imported feed, which is advisory by construction.

See backend/intel_exchange.py for why imported signals are quarantined.
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request

from .. import intel_exchange as exchange
from ..config import settings
from ..coordination_join import JoinRejected, verify_signed_event
from ..db import get_db
from ..deps import _parse_body

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intel/exchange", tags=["federation"])

_PULL_TIMEOUT = 20.0


async def require_admin(x_admin_key: Optional[str] = Header(None)) -> bool:
    """Configuring who receives your alpha is an operator decision, not an
    agent one."""
    import hmac

    if not settings.ADMIN_KEY:
        raise HTTPException(503, "No admin key configured on this instance")
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.ADMIN_KEY):
        raise HTTPException(401, "Valid X-Admin-Key required")
    return True


# ── operator: agreements ─────────────────────────────────────────────────────

@router.get("/agreements")
async def get_agreements(_: bool = Depends(require_admin)):
    """Every intel-sharing arrangement this instance has, both directions."""
    agreements = await exchange.list_agreements()
    return {
        "agreements": agreements,
        "count": len(agreements),
        "policy": {
            "auto_execution_threshold": exchange.AUTO_EXECUTION_THRESHOLD,
            "import_conviction_ceiling": exchange.IMPORT_CONVICTION_CEILING,
            "note": (
                "No trust tier auto-executes. Imported conviction is clamped below "
                "the auto-order threshold, and turning a peer's signal into an order "
                "is always a local, explicit act."
            ),
        },
    }


@router.post("/agreements")
async def put_agreement(
    peer_id: int = Form(...),
    direction: str = Form(...),
    sources: str = Form("[]"),
    signal_types: str = Form("[]"),
    min_conviction: float = Form(0.0),
    trust_tier: str = Form(exchange.TIER_ADVISORY),
    status: str = Form("active"),
    note: str = Form(""),
    _: bool = Depends(require_admin),
):
    """Declare what you will share with, or take from, one peer.

    Both halves must exist for signals to move: your `export` agreement plus
    that peer's own `import` agreement naming you. Neither side can start a
    flow on its own.
    """
    try:
        agreement = await exchange.set_agreement(
            peer_id=peer_id, direction=direction, sources=sources,
            signal_types=signal_types, min_conviction=min_conviction,
            trust_tier=trust_tier, status=status, note=note,
        )
    except exchange.ExchangeRefused as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "agreement": agreement,
        "reminder": (
            "The peer must also configure an import agreement naming this instance "
            "before anything flows."
            if direction == "export" else
            "This peer must also configure an export agreement naming this instance "
            "before anything flows."
        ),
    }


@router.delete("/agreements/{peer_id}/{direction}")
async def revoke_agreement(peer_id: int, direction: str, _: bool = Depends(require_admin)):
    """Stop sharing. Takes effect on the peer's next request — nothing is
    cached on their side by this design."""
    try:
        await exchange.set_agreement(peer_id=peer_id, direction=direction, status="revoked")
    except exchange.ExchangeRefused as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"peer_id": peer_id, "direction": direction, "status": "revoked"}


# ── peer-facing: export ──────────────────────────────────────────────────────

@router.post("/challenge")
async def export_challenge(request: Request):
    """Step 1 for a pulling peer: get a nonce to sign.

    Unauthenticated by necessity — the caller proves who it is by signing
    this. Issuing a nonce reveals nothing.
    """
    body = await _parse_body(request)
    pubkey = str(body.get("pubkey") or "").strip().lower()
    if len(pubkey) != 64:
        raise HTTPException(422, "pubkey (64 hex chars) is required")

    import secrets
    import time

    challenge = secrets.token_hex(32)
    expires_at = int(time.time()) + exchange.CHALLENGE_TTL_SECONDS
    async with get_db() as db:
        await db.execute(
            "INSERT INTO exchange_challenges (challenge, peer_pubkey, expires_at) VALUES (?,?,?)",
            (challenge, pubkey, expires_at),
        )
        await db.commit()
    return {"challenge": challenge, "expires_at": expires_at, "kind": 22242}


@router.post("/export")
async def export_signals(request: Request):
    """Step 2: a peer collects what this instance agreed to give it.

    Authenticated against the pubkey pinned for that peer in
    `federation_peers.nostr_pubkey`. A peer with no pinned pubkey cannot
    authenticate and therefore cannot pull — which is the correct failure:
    an unauthenticated caller must never receive signals.
    """
    import time

    body = await _parse_body(request)
    signed = body.get("signed_event")
    challenge = str(body.get("challenge") or "").strip()
    since = int(body.get("since") or 0)
    limit = int(body.get("limit") or 100)

    if not isinstance(signed, dict) or not challenge:
        raise HTTPException(422, "signed_event and challenge are required")

    async with get_db() as db:
        cur = await db.execute(
            "SELECT peer_pubkey, expires_at, consumed_at FROM exchange_challenges WHERE challenge=?",
            (challenge,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(401, "unknown challenge")
    peer_pubkey, expires_at, consumed_at = row
    if consumed_at:
        raise HTTPException(401, "challenge already used")
    if int(expires_at) < int(time.time()):
        raise HTTPException(401, "challenge expired")

    try:
        verify_signed_event(signed)
    except JoinRejected as exc:
        raise HTTPException(401, str(exc)) from exc
    if str(signed.get("pubkey", "")).lower() != peer_pubkey:
        raise HTTPException(401, "signed by a different key than the challenge was issued to")
    presented = next(
        (t[1] for t in signed.get("tags", []) if t and len(t) >= 2 and t[0] == "challenge"), None
    )
    if presented != challenge:
        raise HTTPException(401, "signed event carries a different challenge")

    async with get_db() as db:
        await db.execute(
            "UPDATE exchange_challenges SET consumed_at=datetime('now') WHERE challenge=?",
            (challenge,),
        )
        cur = await db.execute(
            "SELECT id, name FROM federation_peers WHERE nostr_pubkey=? AND flagged=0", (peer_pubkey,)
        )
        peer = await cur.fetchone()
        await db.commit()

    if not peer:
        # Same response whether the peer is unknown or flagged: a caller
        # should not be able to probe which.
        raise HTTPException(403, "no intel agreement for this identity")

    try:
        signals = await exchange.signals_for_peer(peer[0], since=since, limit=limit)
    except exchange.ExchangeRefused as exc:
        raise HTTPException(403, str(exc)) from exc

    logger.info("intel exchange: exported %d signals to peer %s", len(signals), peer[1])
    return {"signals": signals, "count": len(signals), "served_at": int(time.time())}


# ── operator: pulling from a peer ────────────────────────────────────────────

@router.post("/pull/{peer_id}")
async def pull_from_peer(peer_id: int, _: bool = Depends(require_admin)):
    """Fetch this instance's entitlement from a peer, and quarantine it.

    Pull rather than push, deliberately: an instance decides when to take
    data in, and nobody can inject into it unsolicited.
    """
    from ..buzz_identity import derive_instance_keypair, public_key_xonly_hex
    from ..buzz_client import build_event

    agreement = await exchange.get_agreement(peer_id, "import")
    if not agreement or agreement["status"] != "active":
        raise HTTPException(409, "No active import agreement for this peer")

    async with get_db() as db:
        cur = await db.execute(
            "SELECT url, api_base, name FROM federation_peers WHERE id=?", (peer_id,)
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "No such peer")
    base = (row[1] or row[0] or "").rstrip("/")
    if not base:
        raise HTTPException(422, "Peer has no reachable URL")

    pk = await derive_instance_keypair()
    pubkey = public_key_xonly_hex(pk)

    async with get_db() as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(remote_ts), 0) FROM imported_signals WHERE peer_id=?", (peer_id,)
        )
        since = (await cur.fetchone())[0] or 0

    try:
        async with httpx.AsyncClient(timeout=_PULL_TIMEOUT) as http:
            ch = await http.post(f"{base}/api/intel/exchange/challenge", json={"pubkey": pubkey})
            ch.raise_for_status()
            challenge = ch.json()["challenge"]

            signed = build_event(
                pk, kind=22242, content="",
                tags=[["relay", base], ["challenge", challenge]],
            )
            resp = await http.post(
                f"{base}/api/intel/exchange/export",
                json={"signed_event": signed, "challenge": challenge, "since": since},
            )
            if resp.status_code == 403:
                raise HTTPException(403, f"Peer has no export agreement for us: {resp.text[:200]}")
            resp.raise_for_status()
            signals = resp.json().get("signals", [])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Could not pull from peer: {exc}") from exc

    try:
        result = await exchange.import_signals(peer_id, signals)
    except exchange.ExchangeRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"pulled": len(signals), "since": since, **result}


# ── reading what came in ─────────────────────────────────────────────────────

@router.get("/imported")
async def imported(
    limit: int = Query(100, ge=1, le=500),
    peer_id: Optional[int] = Query(None),
    tier: Optional[str] = Query(None),
    _: bool = Depends(require_admin),
):
    """Signals other instances shared with this one. Advisory."""
    rows = await exchange.list_imported(limit=limit, peer_id=peer_id, tier=tier)
    return {
        "imported": rows,
        "count": len(rows),
        "advisory": True,
        "note": "These are not in the local signal pool and cannot trigger an order.",
    }


@router.post("/imported/{imported_id}/promote")
async def promote(imported_id: int, _: bool = Depends(require_admin)):
    """Surface one imported signal in the local feed, attributed to its peer.

    The only path from a peer's opinion into this instance's own feed, and it
    is one signal at a time by hand. Even promoted, it stays below the
    auto-execution threshold.
    """
    try:
        return await exchange.promote_imported(imported_id)
    except exchange.ExchangeRefused as exc:
        raise HTTPException(404, str(exc)) from exc
