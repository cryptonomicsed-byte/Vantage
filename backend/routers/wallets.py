"""Agent Wallets API — Agent-first wallet creation, signing, and management.

Agents can:
- Create custom wallets (private key encrypted server-side)
- Create Alchemy agent wallets (session token server-side)
- List/view their wallets (no private key exposure)
- Sign transactions (Vantage signs on their behalf)
- Monitor positions (real-time balances + holdings)

Private keys and Alchemy tokens never exposed to agents.
"""
import os
import json
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Header, HTTPException, Query, Body
from pydantic import BaseModel
import aiosqlite
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

from ..config import settings
from ..crypto_utils import encrypt_key_for_agent, decrypt_key_for_agent
from ..hd_wallet import derive_multichain_wallet, generate_mnemonic

router = APIRouter(prefix="/api/agents", tags=["wallets"])
# Was defaulting to a relative "backend/data/vantage.db" -- under this
# process's actual WorkingDirectory that resolved to a stale, orphaned
# SQLite file completely disconnected from the real database every other
# router uses (settings.DATA_DIR). Fixed to match.
DB_PATH = os.environ.get("DB_PATH") or str(settings.DATA_DIR / "vantage.db")
ALCHEMY_KEY = os.environ.get("ALCHEMY_API_KEY", "")
ALCHEMY_WALLET_ADDRESS = os.environ.get("ALCHEMY_WALLET_ADDRESS", "")


# ── Models ────────────────────────────────────────────────────────────────
class WalletCreateRequest(BaseModel):
    type: str  # "custom" or "alchemy"
    name: str
    auto_sign: bool = False


class SignTransactionRequest(BaseModel):
    transaction: Dict[str, Any]
    intent: str  # "trade_order", "send_token", etc.
    request_id: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────
async def _db():
    """Real async connection with busy_timeout -- every endpoint in this
    router used to open a blocking sqlite3.connect() (no busy_timeout at
    all) inside an `async def` handler, stalling the whole event loop for
    every other in-flight request while it ran, and failing outright with
    "database is locked" under any real write concurrency."""
    conn = await aiosqlite.connect(DB_PATH)
    await conn.execute("PRAGMA busy_timeout=20000")
    return conn


async def get_agent_id(x_agent_key: str) -> Optional[int]:
    """Resolve X-Agent-Key header to agent_id.

    Was comparing the raw header value directly against agents.api_key --
    but that column stores a SHA-256 hash of the key (same as every other
    auth path in this app, e.g. deps.py's get_agent), so this comparison
    could never match a real agent. Every endpoint in this router has been
    unconditionally 401ing for real callers. Fixed to hash first."""
    try:
        hashed = hashlib.sha256(x_agent_key.encode()).hexdigest()
        db = await _db()
        try:
            cur = await db.execute("SELECT id FROM agents WHERE api_key = ?", (hashed,))
            row = await cur.fetchone()
            return row[0] if row else None
        finally:
            await db.close()
    except Exception:
        return None


async def init_wallet_tables():
    """Create wallet tables if they don't exist."""
    try:
        db = await _db()
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_wallets (
                    id TEXT PRIMARY KEY,
                    agent_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    address TEXT NOT NULL,

                    private_key_encrypted TEXT,
                    private_key_salt TEXT,

                    alchemy_session_token TEXT,
                    alchemy_capabilities TEXT,
                    alchemy_approval_expires_at TEXT,

                    name TEXT,
                    network TEXT DEFAULT 'solana',
                    created_at TEXT,
                    last_used_at TEXT,

                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            # New wallets get a real BIP-39 mnemonic (sealed the same
            # AES-256-GCM way private_key_encrypted always was) plus
            # derived addresses for the other chains vanity-cloakseed's
            # scheme covers. Additive-only migration -- existing rows are
            # never touched; they just keep derivation_scheme='legacy'
            # (no mnemonic, single Solana-only random keypair, exactly as
            # before) since there's no mnemonic to recover them from.
            for col, ddl in [
                ("mnemonic_encrypted", "TEXT"),
                ("derivation_scheme", "TEXT DEFAULT 'legacy'"),
                ("chain_addresses", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE agent_wallets ADD COLUMN {col} {ddl}")
                except Exception:
                    pass
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_wallet_signatures (
                    id TEXT PRIMARY KEY,
                    wallet_id TEXT NOT NULL,
                    agent_id INTEGER NOT NULL,

                    intent TEXT,
                    transaction_preview TEXT,
                    signed_tx TEXT,

                    vantage_signed BOOLEAN,
                    agent_approved BOOLEAN,

                    created_at TEXT,

                    FOREIGN KEY (wallet_id) REFERENCES agent_wallets(id),
                    FOREIGN KEY (agent_id) REFERENCES agents(id)
                )
            """)
            await db.commit()
        finally:
            await db.close()
    except Exception as e:
        print(f"Wallet table init failed: {e}")


def encrypt_private_key(private_key: str, agent_id: int, api_key: str) -> str:
    """Real AES-256-GCM encryption (backend/crypto_utils.py), keyed off the
    agent's own API key -- was previously base64(key + salt), fully
    recoverable by anyone with database read access. No salt param needed
    anymore: crypto_utils derives + embeds its own per-call salt."""
    return encrypt_key_for_agent(private_key, {"id": agent_id, "api_key": api_key})


def decrypt_private_key(encrypted: str, agent_id: int, api_key: str) -> Optional[str]:
    """Real AES-256-GCM decryption, requires the agent's own API key --
    matches crypto_utils's design principle: the API key is the only
    secret needed, no master key exists to compromise."""
    try:
        return decrypt_key_for_agent(encrypted, {"id": agent_id, "api_key": api_key})
    except Exception:
        return None


# ── Endpoints ────────────────────────────────────────────────────────────
@router.post("/{agent_id}/wallets")
async def create_wallet(
    agent_id: int,
    request: WalletCreateRequest,
    x_agent_key: str = Header(...)
):
    """Create a wallet for an agent (custom or Alchemy)."""
    # Verify agent owns this key
    real_agent_id = await get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    await init_wallet_tables()
    wallet_id = f"wal_{request.type}_{secrets.token_hex(8)}"
    db = await _db()

    try:
        if request.type == "custom":
            # Real BIP-39 mnemonic -> BIP-32/SLIP-0010 derivation (same
            # scheme as ~/vanity-cloakseed/src/utils/hdWallet.ts +
            # chains.ts -- see backend/hd_wallet.py for the exact paths
            # and the one deliberate deviation: real ed25519 SLIP-0010 for
            # Solana instead of that reference tool's secp256k1-for-
            # everything shortcut, since this wallet actually custodies
            # funds). Was previously a bare, unrecoverable solders.Keypair()
            # -- no mnemonic, no recovery, Solana-only. The mnemonic is the
            # one secret that can regenerate every chain's key later; it's
            # sealed the same AES-256-GCM way the Solana private key
            # always was.
            mnemonic = generate_mnemonic()
            wallet = derive_multichain_wallet(mnemonic)
            keypair = wallet.solana_keypair
            private_key = base58.b58encode(bytes(keypair)).decode()
            encrypted_key = encrypt_private_key(private_key, agent_id, x_agent_key)
            encrypted_mnemonic = encrypt_private_key(mnemonic, agent_id, x_agent_key)

            # Real derived address (base58 pubkey), not random hex
            address = str(keypair.pubkey())

            await db.execute("""
                INSERT INTO agent_wallets
                (id, agent_id, type, address, private_key_encrypted, mnemonic_encrypted,
                 derivation_scheme, chain_addresses, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                wallet_id, agent_id, "custom", address, encrypted_key, encrypted_mnemonic,
                "bip39-slip10-multichain", json.dumps(wallet.chain_addresses),
                request.name, datetime.utcnow().isoformat(),
            ))

            response = {
                "wallet_id": wallet_id,
                "agent_id": agent_id,
                "type": "custom",
                "address": address,
                "chain_addresses": wallet.chain_addresses,
                "created_at": datetime.utcnow().isoformat(),
                "balance_tracking": True,
                "private_key_stored": True,
                "private_key_exposed": False,
                "mnemonic_stored": True,
                "mnemonic_exposed": False,
            }

        elif request.type == "alchemy":
            if not ALCHEMY_WALLET_ADDRESS:
                raise HTTPException(status_code=500, detail="Alchemy not configured")

            await db.execute("""
                INSERT INTO agent_wallets
                (id, agent_id, type, address, alchemy_session_token, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (wallet_id, agent_id, "alchemy", ALCHEMY_WALLET_ADDRESS, "", request.name, datetime.utcnow().isoformat()))

            response = {
                "wallet_id": wallet_id,
                "agent_id": agent_id,
                "type": "alchemy",
                "address": ALCHEMY_WALLET_ADDRESS,
                "session_status": "pending_approval",
                "session_url": "https://dashboard.alchemy.com/agents/approve",
                "requires_dashboard_approval": True,
                "created_at": datetime.utcnow().isoformat()
            }

        else:
            raise HTTPException(status_code=400, detail="Invalid wallet type")

        await db.commit()
        return response

    except HTTPException:
        # Was falling through to the broad `except Exception` below, which
        # wrapped even intentional 400s (invalid type)/500s (Alchemy not
        # configured) into a generic 500 -- re-raise as-is instead.
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.get("/{agent_id}/wallets")
async def list_wallets(
    agent_id: int,
    x_agent_key: str = Header(...)
):
    """List all wallets for an agent."""
    real_agent_id = await get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    await init_wallet_tables()
    db = await _db()
    db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        cur = await db.execute(
            """
            SELECT id, type, address, name, network, created_at, last_used_at
            FROM agent_wallets
            WHERE agent_id = ?
            ORDER BY created_at DESC
            """,
            (agent_id,)
        )
        rows = await cur.fetchall()

        return {
            "agent_id": agent_id,
            "wallets": rows or []
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.get("/{agent_id}/wallets/{wallet_id}")
async def get_wallet(
    agent_id: int,
    wallet_id: str,
    x_agent_key: str = Header(...)
):
    """Get wallet details (NO private key exposed)."""
    real_agent_id = await get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    await init_wallet_tables()
    db = await _db()
    db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))

    try:
        cur = await db.execute(
            """
            SELECT id, type, address, name, network, created_at, last_used_at
            FROM agent_wallets
            WHERE id = ? AND agent_id = ?
            """,
            (wallet_id, agent_id)
        )
        wallet = await cur.fetchone()

        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        wallet["private_key_stored"] = True
        wallet["private_key_exposed"] = False  # ← Never exposed
        return wallet

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/{agent_id}/wallets/{wallet_id}/sign")
async def sign_transaction(
    agent_id: int,
    wallet_id: str,
    request: SignTransactionRequest,
    x_agent_key: str = Header(...)
):
    """Agent signs a transaction (Vantage signs on their behalf, private key never exposed)."""
    real_agent_id = await get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    await init_wallet_tables()
    db = await _db()

    try:
        # Verify wallet exists and belongs to agent
        cur = await db.execute(
            "SELECT id, type, address, private_key_encrypted FROM agent_wallets WHERE id = ? AND agent_id = ?",
            (wallet_id, agent_id)
        )
        wallet = await cur.fetchone()

        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        wallet_type, wallet_address, encrypted_key = wallet[1], wallet[2], wallet[3]

        if wallet_type != "custom" or not encrypted_key:
            # Alchemy wallets are session-token signed on Alchemy's side
            # (see the /alchemy/approve endpoint) -- this endpoint only
            # holds the real private key for "custom" wallets.
            raise HTTPException(
                status_code=400,
                detail=f"Wallet type '{wallet_type}' has no locally-held key to sign with here"
            )

        # Create signature record
        sig_id = f"sig_{secrets.token_hex(16)}"
        canonical_tx = json.dumps(request.transaction, sort_keys=True, separators=(",", ":"))
        tx_preview = canonical_tx[:100]

        # Real ed25519 signature: decrypt this agent's own key (never
        # leaves this request), sign the canonical transaction bytes, and
        # discard the plaintext key immediately. Verifiable by anyone
        # against `wallet_address` -- was previously an unrelated random
        # hex string with no cryptographic connection to the wallet at all.
        plaintext_key = decrypt_private_key(encrypted_key, agent_id, x_agent_key)
        if not plaintext_key:
            raise HTTPException(status_code=500, detail="Failed to decrypt wallet key")
        try:
            keypair = Keypair.from_base58_string(plaintext_key)

            tx_b64 = request.transaction.get("tx_b64")
            if tx_b64:
                # A real unsigned Solana transaction was supplied (same
                # tx_b64 convention trading.py's live execution path uses)
                # -- sign it as an actual VersionedTransaction so the result
                # is submittable on-chain, not just a signature over the
                # JSON description of the intent.
                from base64 import b64decode, b64encode
                from solders.transaction import VersionedTransaction
                from solders.message import to_bytes_versioned

                unsigned = VersionedTransaction.from_bytes(b64decode(tx_b64))
                sig = keypair.sign_message(to_bytes_versioned(unsigned.message))
                signed = VersionedTransaction.populate(unsigned.message, [sig])
                signed_tx = b64encode(bytes(signed)).decode()
            else:
                signature: Signature = keypair.sign_message(canonical_tx.encode())
                signed_tx = str(signature)
        finally:
            del plaintext_key

        await db.execute("""
            INSERT INTO agent_wallet_signatures
            (id, wallet_id, agent_id, intent, transaction_preview, signed_tx, vantage_signed, agent_approved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sig_id, wallet_id, agent_id, request.intent, tx_preview, signed_tx, True, False, datetime.utcnow().isoformat()))

        await db.commit()

        return {
            "signed_tx": signed_tx,
            "tx_hash_preview": signed_tx[:10] + "...",
            "signer_address": wallet_address,
            "signature_scheme": "ed25519",
            "agent_signed": False,
            "vantage_signed": True,
            "intent": request.intent,
            "request_id": request.request_id,
            "timestamp": datetime.utcnow().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/{agent_id}/wallets/{wallet_id}/alchemy/approve")
async def approve_alchemy_session(
    agent_id: int,
    wallet_id: str,
    capabilities: List[str] = Body(...),
    expires_in_days: int = 30,
    x_agent_key: str = Header(...)
):
    """Agent requests Alchemy session approval."""
    real_agent_id = await get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    await init_wallet_tables()
    db = await _db()

    try:
        cur = await db.execute(
            "SELECT id, type FROM agent_wallets WHERE id = ? AND agent_id = ?",
            (wallet_id, agent_id)
        )
        wallet = await cur.fetchone()

        if not wallet or wallet[1] != "alchemy":
            raise HTTPException(status_code=404, detail="Alchemy wallet not found")

        expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat()

        await db.execute("""
            UPDATE agent_wallets
            SET alchemy_capabilities = ?, alchemy_approval_expires_at = ?
            WHERE id = ?
        """, (json.dumps(capabilities), expires_at, wallet_id))

        await db.commit()

        return {
            "wallet_id": wallet_id,
            "approval_url": "https://dashboard.alchemy.com/agents/approve",
            "status": "pending_approval",
            "capabilities_requested": capabilities,
            "expires_at": expires_at,
            "note": "Agent must visit approval_url to activate Alchemy session"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# Table init now happens from main.py's async startup lifespan (see
# init_wallet_tables() call there) -- this function is async now (real
# aiosqlite instead of blocking sqlite3.connect()), so it can no longer
# run bare at module-import time with no event loop present.
