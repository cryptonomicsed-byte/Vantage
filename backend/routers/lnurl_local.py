"""Local LNURL-pay + NIP-57 zap server in front of the regtest lnd-b node.

Why: `routers/live.py` builds a NIP-57 zap request and then resolves the host's
`lud16` lightning address through LNURL-pay. Until now there was no LNURL server
anywhere in the stack, so `POST /api/live/{id}/zap` could not produce an invoice
for a node we control — zaps depended on an external custodial provider.

This module makes lnd-b the merchant:
  GET  /.well-known/lnurlp/<user>     LNURL-pay params, allowsNostr + nostrPubkey
  GET  /api/lnurl/callback            validates the 9734 zap request, mints a real
                                      BOLT11 invoice on lnd-b
  bg   _receipt_watcher               polls lnd-b; when an invoice settles it
                                      publishes the kind-9735 zap receipt

NIP-57 correctness notes:
  * the zap request's `amount` tag (millisats) MUST equal the callback's `amount`,
    otherwise a caller pays a small invoice while claiming a large zap;
  * the request's `p` tag must be OUR nostr pubkey, and its BIP340 signature must
    verify over the event id — anything else is a forged receipt source;
  * the receipt is signed by the same key advertised as `nostrPubkey`.
"""
import asyncio
import base64
import json
import logging
import os
import time
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from coincurve import PublicKeyXOnly

from ..buzz_identity import (
    derive_instance_keypair,
    public_key_xonly_hex,
)
from ..db import get_db
from ..nostr.client import NostrSession
from ..buzz_registration import RELAY_WS_URL

logger = logging.getLogger(__name__)

router = APIRouter(tags=["lnurl"])

LNCLI = os.environ.get("VANTAGE_LNCLI_B", "/usr/local/bin/lncli-b")
PUBLIC_URL = (os.environ.get("VANTAGE_PUBLIC_URL") or "https://omokoda.duckdns.org").rstrip("/")
LNURL_USER = os.environ.get("VANTAGE_LNURL_USER", "vantage")

MIN_SENDABLE_MSAT = 1_000          # 1 sat
MAX_SENDABLE_MSAT = 100_000_000    # 100k sat

KIND_ZAP_REQUEST = 9734
KIND_ZAP_RECEIPT = 9735


async def _lncli(*args: str, timeout: float = 20.0) -> dict:
    proc = await asyncio.create_subprocess_exec(
        LNCLI, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"lncli {' '.join(args[:2])} failed: {err.decode()[:300]}")
    return json.loads(out.decode() or "{}")


async def _ensure_table() -> None:
    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS lnurl_zaps (
                payment_hash TEXT PRIMARY KEY,
                amount_msat  INTEGER NOT NULL,
                zap_request  TEXT,
                sender_pubkey TEXT,
                settled      INTEGER NOT NULL DEFAULT 0,
                receipt_id   TEXT,
                created_at   INTEGER NOT NULL
            )
            """
        )
        await db.commit()


def _decode_zap_request(blob: str) -> dict:
    """Base64 (url-safe, unpadded) → zap request JSON."""
    padded = blob + "=" * (-len(blob) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode())
        request = json.loads(raw.decode())
    except Exception:
        raise HTTPException(status_code=400, detail="nostr param is not base64 JSON")
    if not isinstance(request, dict):
        raise HTTPException(status_code=400, detail="zap request must be a JSON object")
    return request


def _tag_value(event: dict, name: str) -> Optional[str]:
    for tag in event.get("tags") or []:
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == name:
            return tag[1]
    return None


def _verify_zap_request(request: dict, our_pubkey: str, amount_msat: int) -> str:
    """Validate the 9734 request; return the sender pubkey."""
    if request.get("kind") != KIND_ZAP_REQUEST:
        raise HTTPException(status_code=400, detail=f"expected kind {KIND_ZAP_REQUEST}")

    recipient = _tag_value(request, "p")
    if recipient != our_pubkey:
        raise HTTPException(status_code=400, detail="zap request p-tag is not this node")

    declared = _tag_value(request, "amount")
    if declared is None:
        raise HTTPException(status_code=400, detail="zap request has no amount tag")
    try:
        declared_msat = int(declared)
    except ValueError:
        raise HTTPException(status_code=400, detail="amount tag is not an integer")
    if declared_msat != amount_msat:
        raise HTTPException(
            status_code=400,
            detail=f"amount mismatch: request says {declared_msat} msat, callback says {amount_msat}",
        )

    # BIP340 verification over the event id.
    event_id = request.get("id")
    sig = request.get("sig")
    sender = request.get("pubkey")
    if not (event_id and sig and sender):
        raise HTTPException(status_code=400, detail="zap request missing id/sig/pubkey")
    try:
        ok = PublicKeyXOnly(bytes.fromhex(sender)).verify(
            bytes.fromhex(sig), bytes.fromhex(event_id)
        )
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=400, detail="zap request signature does not verify")
    return sender


@router.get("/.well-known/lnurlp/{username}")
async def lnurl_pay_params(username: str) -> dict:
    if username != LNURL_USER:
        raise HTTPException(status_code=404, detail="unknown lightning user")
    pk = await derive_instance_keypair()
    nostr_pubkey = public_key_xonly_hex(pk)
    host = PUBLIC_URL.split("://", 1)[-1]
    return {
        "callback": f"{PUBLIC_URL}/api/lnurl/callback",
        "minSendable": MIN_SENDABLE_MSAT,
        "maxSendable": MAX_SENDABLE_MSAT,
        "metadata": json.dumps(
            [
                ["text/plain", "Vantage zap"],
                ["text/identifier", f"{username}@{host}"],
            ]
        ),
        "tag": "payRequest",
        "allowsNostr": True,
        "nostrPubkey": nostr_pubkey,
    }


@router.get("/api/lnurl/callback")
async def lnurl_callback(
    amount: int = Query(..., ge=MIN_SENDABLE_MSAT, le=MAX_SENDABLE_MSAT, description="millisats"),
    nostr: Optional[str] = Query(None, description="base64url zap request"),
) -> dict:
    await _ensure_table()
    pk = await derive_instance_keypair()
    our_pubkey = public_key_xonly_hex(pk)

    sender = None
    request_json = None
    if nostr:
        request = _decode_zap_request(nostr)
        sender = _verify_zap_request(request, our_pubkey, amount)
        request_json = json.dumps(request, separators=(",", ":"))

    sats = amount // 1000
    if sats < 1:
        raise HTTPException(status_code=400, detail="amount rounds to zero sats")

    memo = f"zap {sats} sat" + (f" from {sender[:8]}" if sender else "")
    invoice = await _lncli("addinvoice", f"--amt={sats}", f"--memo={memo}")
    payment_request = invoice.get("payment_request")
    payment_hash = invoice.get("r_hash")
    if not payment_request or not payment_hash:
        raise HTTPException(status_code=502, detail="lnd returned no invoice")

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO lnurl_zaps "
            "(payment_hash, amount_msat, zap_request, sender_pubkey, settled, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (payment_hash, amount, request_json, sender, int(time.time())),
        )
        await db.commit()

    logger.info("LNURL invoice minted: %s sat hash=%s", sats, payment_hash[:12])
    return {"pr": payment_request, "routes": []}


async def _publish_receipt(row: dict) -> Optional[str]:
    """Publish the kind-9735 receipt for a settled zap."""
    pk = await derive_instance_keypair()
    our_pubkey = public_key_xonly_hex(pk)

    tags = [
        ["p", row["sender_pubkey"]],
        ["P", our_pubkey],
    ]
    request = json.loads(row["zap_request"]) if row.get("zap_request") else None
    if request:
        if request.get("id"):
            tags.append(["e", request["id"]])
        tags.append(["description", json.dumps(request, separators=(",", ":"))])
    tags.append(["bolt11", row.get("bolt11") or ""])
    tags.append(["amount", str(row["amount_msat"])])

    session = NostrSession(RELAY_WS_URL, pk)
    await session.connect()
    await session.authenticate()
    try:
        result = await session.publish(KIND_ZAP_RECEIPT, "", tags=tags)
    finally:
        await session.close()

    ack = (result or {}).get("ack") or []
    if not (len(ack) > 2 and ack[2]):
        logger.warning("9735 receipt rejected by relay: %s", ack)
        return None
    return ((result or {}).get("event") or {}).get("id")


async def _receipt_watcher(interval: float = 5.0) -> None:
    """Poll lnd for settled invoices that still owe a receipt."""
    logger.info("lnurl receipt watcher started (every %ss)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            await _ensure_table()
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT payment_hash, amount_msat, zap_request, sender_pubkey "
                    "FROM lnurl_zaps WHERE settled = 0 AND sender_pubkey IS NOT NULL"
                ) as cur:
                    pending = [dict(r) for r in await cur.fetchall()]
            if not pending:
                continue

            invoices = await _lncli("listinvoices")
            settled = {
                inv.get("r_hash"): inv
                for inv in invoices.get("invoices", [])
                if inv.get("state") == "SETTLED"
            }
            for row in pending:
                if row["payment_hash"] not in settled:
                    continue
                row["bolt11"] = settled[row["payment_hash"]].get("payment_request") or ""
                receipt_id = await _publish_receipt(row)
                if receipt_id:
                    async with get_db() as db:
                        await db.execute(
                            "UPDATE lnurl_zaps SET settled = 1, receipt_id = ? WHERE payment_hash = ?",
                            (receipt_id, row["payment_hash"]),
                        )
                        await db.commit()
                    logger.info("zap receipt published: %s", receipt_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - watcher must never die
            logger.warning("lnurl receipt watcher error: %s", exc)
