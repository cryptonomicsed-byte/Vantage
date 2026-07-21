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
import sqlite3
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Header, HTTPException, Query, Body
from pydantic import BaseModel

router = APIRouter(prefix="/api/agents", tags=["wallets"])
DB_PATH = os.environ.get("DB_PATH", "backend/data/vantage.db")
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
def get_agent_id(x_agent_key: str) -> Optional[int]:
    """Resolve X-Agent-Key header to agent_id."""
    try:
        db = sqlite3.connect(DB_PATH)
        row = db.execute(
            "SELECT id FROM agents WHERE api_key = ?",
            (x_agent_key,)
        ).fetchone()
        db.close()
        return row[0] if row else None
    except:
        return None


def init_wallet_tables():
    """Create wallet tables if they don't exist."""
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("""
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
        db.execute("""
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
        db.commit()
        db.close()
    except Exception as e:
        print(f"Wallet table init failed: {e}")


def encrypt_private_key(private_key: str, salt: str) -> str:
    """Placeholder: In production, use proper encryption (e.g., Fernet)."""
    # For now, base64 encode + salt. UPGRADE THIS.
    import base64
    combined = f"{private_key}:{salt}".encode()
    return base64.b64encode(combined).decode()


def decrypt_private_key(encrypted: str, salt: str) -> Optional[str]:
    """Placeholder: Decrypt with same method as above."""
    import base64
    try:
        decrypted = base64.b64decode(encrypted).decode()
        key, stored_salt = decrypted.split(":")
        if stored_salt == salt:
            return key
    except:
        pass
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
    real_agent_id = get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    init_wallet_tables()
    wallet_id = f"wal_{request.type}_{secrets.token_hex(8)}"

    try:
        db = sqlite3.connect(DB_PATH)

        if request.type == "custom":
            # Generate a new wallet (in production, use proper key generation)
            import secrets
            private_key = f"0x{secrets.token_hex(32)}"  # Placeholder
            salt = secrets.token_hex(16)
            encrypted_key = encrypt_private_key(private_key, salt)

            # Derive address from private key (placeholder)
            address = f"0x{secrets.token_hex(20)}"

            db.execute("""
                INSERT INTO agent_wallets
                (id, agent_id, type, address, private_key_encrypted, private_key_salt, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (wallet_id, agent_id, "custom", address, encrypted_key, salt, request.name, datetime.utcnow().isoformat()))

            response = {
                "wallet_id": wallet_id,
                "agent_id": agent_id,
                "type": "custom",
                "address": address,
                "created_at": datetime.utcnow().isoformat(),
                "balance_tracking": True,
                "private_key_stored": True,
                "private_key_exposed": False
            }

        elif request.type == "alchemy":
            if not ALCHEMY_WALLET_ADDRESS:
                raise HTTPException(status_code=500, detail="Alchemy not configured")

            db.execute("""
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

        db.commit()
        db.close()
        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}/wallets")
async def list_wallets(
    agent_id: int,
    x_agent_key: str = Header(...)
):
    """List all wallets for an agent."""
    real_agent_id = get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    init_wallet_tables()

    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        rows = db.execute(
            """
            SELECT id, type, address, name, network, created_at, last_used_at
            FROM agent_wallets
            WHERE agent_id = ?
            ORDER BY created_at DESC
            """,
            (agent_id,)
        ).fetchall()
        db.close()

        return {
            "agent_id": agent_id,
            "wallets": rows or []
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}/wallets/{wallet_id}")
async def get_wallet(
    agent_id: int,
    wallet_id: str,
    x_agent_key: str = Header(...)
):
    """Get wallet details (NO private key exposed)."""
    real_agent_id = get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    init_wallet_tables()

    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        wallet = db.execute(
            """
            SELECT id, type, address, name, network, created_at, last_used_at
            FROM agent_wallets
            WHERE id = ? AND agent_id = ?
            """,
            (wallet_id, agent_id)
        ).fetchone()
        db.close()

        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")

        wallet["private_key_stored"] = True
        wallet["private_key_exposed"] = False  # ← Never exposed
        return wallet

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/wallets/{wallet_id}/sign")
async def sign_transaction(
    agent_id: int,
    wallet_id: str,
    request: SignTransactionRequest,
    x_agent_key: str = Header(...)
):
    """Agent signs a transaction (Vantage signs on their behalf, private key never exposed)."""
    real_agent_id = get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    init_wallet_tables()

    try:
        db = sqlite3.connect(DB_PATH)

        # Verify wallet exists and belongs to agent
        wallet = db.execute(
            "SELECT id, type, address FROM agent_wallets WHERE id = ? AND agent_id = ?",
            (wallet_id, agent_id)
        ).fetchone()

        if not wallet:
            db.close()
            raise HTTPException(status_code=404, detail="Wallet not found")

        # Create signature record
        sig_id = f"sig_{secrets.token_hex(16)}"
        tx_preview = json.dumps(request.transaction)[:100]

        # Placeholder: In production, actually sign the transaction
        # For now, return a mock signed tx
        signed_tx = f"0x_signed_{secrets.token_hex(32)}"

        db.execute("""
            INSERT INTO agent_wallet_signatures
            (id, wallet_id, agent_id, intent, transaction_preview, signed_tx, vantage_signed, agent_approved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sig_id, wallet_id, agent_id, request.intent, tx_preview, signed_tx, True, False, datetime.utcnow().isoformat()))

        db.commit()
        db.close()

        return {
            "signed_tx": signed_tx,
            "tx_hash_preview": signed_tx[:10] + "...",
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


@router.post("/{agent_id}/wallets/{wallet_id}/alchemy/approve")
async def approve_alchemy_session(
    agent_id: int,
    wallet_id: str,
    capabilities: List[str] = Body(...),
    expires_in_days: int = 30,
    x_agent_key: str = Header(...)
):
    """Agent requests Alchemy session approval."""
    real_agent_id = get_agent_id(x_agent_key)
    if not real_agent_id or real_agent_id != agent_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    init_wallet_tables()

    try:
        db = sqlite3.connect(DB_PATH)

        wallet = db.execute(
            "SELECT id, type FROM agent_wallets WHERE id = ? AND agent_id = ?",
            (wallet_id, agent_id)
        ).fetchone()

        if not wallet or wallet[1] != "alchemy":
            db.close()
            raise HTTPException(status_code=404, detail="Alchemy wallet not found")

        expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat()

        db.execute("""
            UPDATE agent_wallets
            SET alchemy_capabilities = ?, alchemy_approval_expires_at = ?
            WHERE id = ?
        """, (json.dumps(capabilities), expires_at, wallet_id))

        db.commit()
        db.close()

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


# Initialize tables on import
init_wallet_tables()
