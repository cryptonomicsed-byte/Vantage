"""Runtime receipts: proof that an artifact was actually executed.

The claim/artifact pair says a principal *said* it did the work. A receipt is
the runtime's signed statement that it really ran the action -- produced by
the agent kernel (Omo-Koda2 `omokoda-core/src/receipt`), hash-chained per
agent, and verifiable here without trusting the sender.

Three checks, and all three have to pass:

1. **The id is recomputed, not read.** The receipt id is BLAKE3 over the
   receipt's own fields in a fixed order, so a receipt whose id does not
   derive from its content is rejected before the signature is even looked
   at. Without this, the signature would only prove somebody signed *an*
   id -- not that the id describes this action.
2. **The signature is Ed25519 over the id, against a pinned key.** The key is
   registered once per principal and pinned; rotation is explicit and
   recorded, so a silent key swap cannot rewrite history.
3. **The chain links.** Each receipt names the previous one. A receipt that
   forks the chain is refused, which makes dropping or reordering a
   principal's receipts detectable rather than free.

And then a fourth, which is Vantage's own rather than the kernel's: **a
receipt only counts against work the same principal holds the claim on.**
Otherwise a valid receipt for one's own unrelated action could be attached to
somebody else's delivery.

**No new event kind.** An accepted receipt is published as kind 1902 -- the
attestation schema the ecosystem already locked -- with `stance: confirm` and
an `e` tag pointing at the artifact event. That schema is explicitly
attestor-agnostic, and a runtime confirming its own execution is precisely an
attestor. See `backend/nostr_kinds.py`.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

from . import work_refs
from .db import get_db
from .nostr_kinds import kind as kind_number

logger = logging.getLogger(__name__)

KIND_ATTESTATION = kind_number("attestation")

#: The order the kernel hashes receipt fields in. Changing this breaks every
#: receipt ever issued, so it is written out rather than derived from a dict.
ID_FIELDS = ("agent_id", "action", "payload", "previous_hash", "merkle_root",
             "timestamp", "nonce")

#: What a chain's first receipt may name as its predecessor. The kernel's
#: store starts with an empty last hash.
GENESIS_PREVIOUS = {"", "0" * 64}


class ReceiptError(ValueError):
    """A receipt that does not verify. The message is the reason, and it is
    meant to be returned to the submitter -- a rejection nobody can diagnose
    is a rejection that gets worked around."""


def _blake3_hex(*parts: bytes) -> str:
    try:
        import blake3
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ReceiptError(
            "receipt verification needs the blake3 package; this instance "
            "cannot accept runtime receipts until it is installed"
        ) from exc
    hasher = blake3.blake3()
    for part in parts:
        hasher.update(part)
    return hasher.hexdigest()


def compute_receipt_id(receipt: dict) -> str:
    """Recompute a receipt's id from its fields.

    Mirrors `Receipt::calculate_id` in the kernel exactly, including that the
    two integers are hashed as their decimal *strings* rather than as bytes.
    That detail is not cosmetic -- hashing them as integers produces a
    different id and every receipt would be rejected.
    """
    missing = [f for f in ID_FIELDS if f not in receipt]
    if missing:
        raise ReceiptError(f"receipt is missing {', '.join(missing)}")
    try:
        parts = [
            str(receipt["agent_id"]).encode(),
            str(receipt["action"]).encode(),
            str(receipt["payload"]).encode(),
            str(receipt["previous_hash"]).encode(),
            str(receipt["merkle_root"]).encode(),
            str(int(receipt["timestamp"])).encode(),
            str(int(receipt["nonce"])).encode(),
        ]
    except (TypeError, ValueError) as exc:
        raise ReceiptError(f"timestamp and nonce must be integers: {exc}") from exc
    return _blake3_hex(*parts)


def verify_signature(receipt_id: str, signature_hex: str, pubkey_hex: str) -> None:
    """Ed25519 over the receipt id's ASCII bytes -- not over its raw hash.

    The kernel signs `receipt_id.as_bytes()`, which is the 64-character hex
    string, so verifying against the decoded 32 bytes would fail every real
    receipt while looking entirely reasonable.
    """
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        key = VerifyKey(bytes.fromhex(pubkey_hex))
    except (ValueError, TypeError) as exc:
        raise ReceiptError(f"invalid receipt public key: {exc}") from exc
    try:
        signature = bytes.fromhex(signature_hex)
    except (ValueError, TypeError) as exc:
        raise ReceiptError(f"invalid signature hex: {exc}") from exc
    try:
        key.verify(receipt_id.encode(), signature)
    except BadSignatureError as exc:
        raise ReceiptError("signature does not verify against the pinned key") from exc


# ── schema ───────────────────────────────────────────────────────────────────

async def init_receipts_db() -> None:
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS receipt_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id INTEGER NOT NULL REFERENCES principals(id),
                pubkey TEXT NOT NULL,
                label TEXT DEFAULT '',
                registered_at TEXT DEFAULT (datetime('now')),
                revoked_at TEXT,
                UNIQUE (principal_id, pubkey)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS runtime_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id TEXT NOT NULL UNIQUE,
                principal_id INTEGER NOT NULL REFERENCES principals(id),
                agent_ref TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                previous_hash TEXT NOT NULL DEFAULT '',
                merkle_root TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL DEFAULT '',
                timestamp INTEGER NOT NULL DEFAULT 0,
                -- TEXT, not INTEGER. The kernel's nonce is a full u64 drawn
                -- at random, and SQLite's INTEGER is signed 64-bit -- so
                -- roughly half of all real receipts overflow it. Storing the
                -- decimal string is also what the id is hashed over, so this
                -- is the closer representation anyway.
                nonce TEXT NOT NULL DEFAULT '0',
                work_ref TEXT DEFAULT '',
                artifact_event_id TEXT DEFAULT '',
                attestation_event_id TEXT DEFAULT '',
                accepted_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_principal "
            "ON runtime_receipts(principal_id, id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_work ON runtime_receipts(work_ref)"
        )
        await db.commit()


# ── key custody ──────────────────────────────────────────────────────────────

async def register_key(principal_id: int, pubkey: str, label: str = "") -> dict:
    """Pin a principal's receipt-signing key.

    Trust on first use, and deliberately so: the kernel's receipt key is
    Ed25519 while its relay identity is secp256k1, so there is no way to
    derive one from the other and no signature over the new key that this
    instance could check against anything it already knows. What it can do is
    refuse to let a second key quietly replace the first -- rotation is a
    separate, recorded act, and the old key stays on file so receipts signed
    under it still verify.
    """
    pubkey = (pubkey or "").strip().lower()
    if len(pubkey) != 64:
        raise ReceiptError("a receipt public key is 32 bytes as 64 hex characters")
    try:
        bytes.fromhex(pubkey)
    except ValueError as exc:
        raise ReceiptError(f"receipt public key is not hex: {exc}") from exc

    async with get_db() as db:
        await db.execute(
            """INSERT OR IGNORE INTO receipt_keys (principal_id, pubkey, label)
               VALUES (?,?,?)""",
            (principal_id, pubkey, label[:80]),
        )
        await db.commit()
    return {"principal_id": principal_id, "pubkey": pubkey, "label": label}


async def revoke_key(principal_id: int, pubkey: str) -> bool:
    """Retire a key. Receipts already accepted under it stay accepted --
    revocation is not a time machine."""
    async with get_db() as db:
        cur = await db.execute(
            """UPDATE receipt_keys SET revoked_at=datetime('now')
                WHERE principal_id=? AND pubkey=? AND revoked_at IS NULL""",
            (principal_id, pubkey.strip().lower()),
        )
        await db.commit()
    return bool(cur.rowcount)


async def active_keys(principal_id: int) -> list[str]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT pubkey FROM receipt_keys
                WHERE principal_id=? AND revoked_at IS NULL ORDER BY id""",
            (principal_id,),
        )
        return [dict(r)["pubkey"] for r in await cur.fetchall()]


# ── the chain ────────────────────────────────────────────────────────────────

async def _already_recorded(receipt_id: str) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT 1 FROM runtime_receipts WHERE receipt_id=?", (receipt_id,)
        )
        return await cur.fetchone() is not None


async def chain_head(principal_id: int) -> Optional[str]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT receipt_id FROM runtime_receipts WHERE principal_id=? ORDER BY id DESC LIMIT 1",
            (principal_id,),
        )
        row = await cur.fetchone()
    return dict(row)["receipt_id"] if row else None


async def submit(
    *, principal: dict, receipt: dict, work_ref: str = "", artifact_event_id: str = "",
) -> dict:
    """Verify a receipt and accept it. Raises ReceiptError with the reason.

    Order matters: the cheap structural checks run before the signature, and
    the signature runs before anything is written, so a malformed submission
    costs a hash and nothing else.
    """
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt must be an object")

    expected_id = compute_receipt_id(receipt)
    claimed_id = str(receipt.get("receipt_id") or "")
    if claimed_id != expected_id:
        raise ReceiptError(
            "receipt_id does not derive from the receipt's own fields "
            f"(computed {expected_id[:16]}…, received {claimed_id[:16] or 'nothing'}…)"
        )

    keys = await active_keys(principal["id"])
    if not keys:
        raise ReceiptError(
            "no receipt key is registered for this principal — register one before "
            "submitting receipts"
        )
    signature = str(receipt.get("signature") or "")
    last_error: Optional[ReceiptError] = None
    for pubkey in keys:
        try:
            verify_signature(expected_id, signature, pubkey)
            break
        except ReceiptError as exc:
            last_error = exc
    else:
        raise last_error or ReceiptError("signature does not verify")

    # A replay is checked before the chain, and the order is the whole point
    # of doing it here rather than leaving it to the UNIQUE constraint: a
    # resubmitted receipt fails the chain check first and would be reported
    # as a fork, which is both wrong and unactionable. "You already sent
    # this" is the accurate answer.
    if await _already_recorded(expected_id):
        raise ReceiptError("this receipt has already been submitted")

    # The chain. A fork is refused rather than merged: two receipts naming the
    # same predecessor means one of them is not part of this history, and
    # accepting both would make the chain decorative.
    head = await chain_head(principal["id"])
    previous = str(receipt.get("previous_hash") or "")
    if head is None:
        if previous not in GENESIS_PREVIOUS and previous != "":
            logger.info("receipts: accepting %s as chain start with previous %s",
                        expected_id[:8], previous[:8])
    elif previous != head:
        raise ReceiptError(
            f"receipt does not extend this principal's chain: it names {previous[:16] or 'nothing'}… "
            f"but the head is {head[:16]}…"
        )

    # Vantage's own rule, not the kernel's: a receipt earns credit only
    # against work this principal actually holds.
    resolved = None
    if work_ref:
        resolved = await work_refs.resolve(work_ref)
        if resolved is None:
            raise ReceiptError(f"{work_ref} does not resolve on this instance")
        holder = await work_refs.claim_holder(resolved.kind, resolved.ref_id)
        if holder is not None and holder != principal["id"]:
            raise ReceiptError(f"{work_ref} is claimed by another principal")

    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO runtime_receipts
                     (receipt_id, principal_id, agent_ref, action, payload, previous_hash,
                      merkle_root, signature, timestamp, nonce, work_ref, artifact_event_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (expected_id, principal["id"], str(receipt.get("agent_id") or ""),
                 str(receipt.get("action") or "")[:200], str(receipt.get("payload") or "")[:200],
                 previous, str(receipt.get("merkle_root") or ""), signature,
                 int(receipt["timestamp"]), str(int(receipt["nonce"])),
                 work_ref, artifact_event_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            # The check above catches the ordinary replay; this catches two
            # concurrent submissions of the same receipt, where both passed
            # it before either wrote.
            raise ReceiptError("this receipt has already been submitted") from exc

    return {
        "receipt_id": expected_id, "accepted": True,
        "work_ref": work_ref or None,
        "chain_position": "start" if head is None else "extends",
        "artifact_event_id": artifact_event_id or None,
    }


async def attest(
    *, channel: dict, guild_slug: str, principal: dict, receipt_id: str,
    artifact_event_id: str, work_ref: str = "",
) -> Optional[str]:
    """Publish the accepted receipt as a kind 1902 attestation.

    Best effort. The receipt is already durable here; failing to broadcast it
    must not un-accept it, so this returns None rather than raising.
    """
    try:
        from .buzz_client import BuzzSession
        from .buzz_registration import RELAY_WS_URL
        from .coordination import signing_key_for_principal

        pk = await signing_key_for_principal(principal)
        if pk is None:
            # A self-custody principal signs its own attestation; this
            # instance publishing one on its behalf would defeat the custody.
            return None

        tags = [
            ["e", artifact_event_id],
            ["stance", "confirm"],
            ["receipt", receipt_id],
            ["h", channel["buzz_channel_id"]],
            ["vg", guild_slug, channel["slug"]],
        ]
        if work_ref:
            tags.append(["vw", work_ref])

        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            result = await sess.publish(
                KIND_ATTESTATION, f"runtime receipt {receipt_id[:16]}", tags=tags
            )
        finally:
            await sess.close()
        event_id = (result.get("event") or {}).get("id")
    except Exception as exc:
        logger.info("receipts: attestation not published for %s: %s", receipt_id[:8], exc)
        return None

    if event_id:
        async with get_db() as db:
            await db.execute(
                "UPDATE runtime_receipts SET attestation_event_id=? WHERE receipt_id=?",
                (event_id, receipt_id),
            )
            await db.commit()
    return event_id


# ── read side ────────────────────────────────────────────────────────────────

async def receipts_for_work(work_ref: str) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT r.*, p.display_name FROM runtime_receipts r
                 JOIN principals p ON p.id = r.principal_id
                WHERE r.work_ref=? ORDER BY r.id""",
            (work_ref,),
        )
        return [dict(row) for row in await cur.fetchall()]


async def chain_for_principal(principal_id: int, limit: int = 50) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT receipt_id, action, previous_hash, work_ref, artifact_event_id,
                      attestation_event_id, timestamp, accepted_at
                 FROM runtime_receipts WHERE principal_id=? ORDER BY id DESC LIMIT ?""",
            (principal_id, limit),
        )
        return [dict(row) for row in await cur.fetchall()]
