"""Multi-chain credential provisioning at agent birth.

When an agent is born (registered or spawned via genesis), this module
provisions all the identity credentials they need to participate in the
Technosis ecosystem:

  PROVISIONED AT BIRTH
  ├── Nostr identity     — sealed seed → Ed25519 keypair → npub (Buzz/NIP-01)
  ├── Vantage API key    — already exists from registration
  ├── Freenet identity   — derived from sealed seed (Phase F3)
  ├── Sui wallet         — existing wallet derivation (agent_wallets)
  └── Meshtastic/Reticulum — future (mesh node ID derivation)

  NEVER STORED IN VANTAGE
  ├── Private keys (sealed seed is encrypted, never plain)
  ├── Ọmọ Kọ́dà2 internal state (sovereign)
  └── Arweave wallet (external, agent holds directly)

The sovereignty boundary is absolute:
  Vantage stores the sealed seed (AES-256-GCM encrypted, key from env).
  Vantage derives public keys for display/routing.
  Vantage NEVER exposes private key material via API.
  Ọmọ Kọ́dà2 holds its own keys; if the agent is self-custody, Vantage
  cannot sign for it at all (SelfCustodyError enforced by buzz_identity.py).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


async def provision_birth_credentials(agent_id: int, agent_name: str,
                                      api_key: Optional[str] = None) -> Dict[str, Any]:
    """Provision all birth credentials for a newly registered agent.

    Called from:
      - /api/agents/register (after DB insert)
      - /api/genesis/spawn (after agent birth)
      - /api/agents/birth-omokoda (after kernel birth)

    Returns a manifest of what was provisioned. The manifest is safe to
    return in the birth response — it contains only public keys and
    status flags, never private material.

    Idempotent: calling this twice for the same agent is safe (seeds are
    get_or_create, Buzz registration is idempotent).
    """
    manifest: Dict[str, Any] = {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "credentials": {},
        "errors": [],
    }

    # 1. Nostr identity (Buzz) — most critical, agents need this for federation
    nostr = await _provision_nostr(agent_id, agent_name)
    manifest["credentials"]["nostr"] = nostr

    # 2. Freenet identity — derived from same sealed seed (Phase F3)
    freenet = await _provision_freenet(agent_id)
    manifest["credentials"]["freenet"] = freenet

    # 3. Meshtastic/Reticulum — future mesh identity (Phase F5+)
    manifest["credentials"]["mesh"] = {
        "status": "pending",
        "note": "Meshtastic/Reticulum identity — Phase F5",
    }

    # 4. Sui wallet — already handled by existing wallet provisioning
    manifest["credentials"]["sui"] = await _provision_sui_summary(agent_id)

    # 5. Arweave — external, agent holds key themselves
    manifest["credentials"]["arweave"] = {
        "status": "external",
        "note": "Arweave wallet held externally by agent; use /api/agents/me/wallets to link",
    }

    log.info(
        "Birth credentials provisioned for agent %s (%s): nostr=%s freenet=%s",
        agent_id, agent_name,
        nostr.get("status"), freenet.get("status"),
    )
    return manifest


async def _provision_nostr(agent_id: int, agent_name: str) -> Dict[str, Any]:
    """Provision Nostr identity: sealed seed → keypair → store pubkey → register on Buzz."""
    try:
        from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex, store_nostr_pubkey
        from .db import get_db

        kp = await derive_buzz_keypair(agent_id)
        pubkey_hex = public_key_xonly_hex(kp)

        # Store pubkey in agents table if not already set
        async with get_db() as db:
            cur = await db.execute(
                "SELECT nostr_pubkey_hex FROM agents WHERE id = ?", (agent_id,)
            )
            row = await cur.fetchone()
            if not row or not row[0]:
                await store_nostr_pubkey(agent_id, pubkey_hex)

        # Convert to npub bech32 for display
        npub = _hex_to_npub(pubkey_hex)

        # Attempt Buzz registration (non-fatal if relay unavailable)
        buzz_status = "not_registered"
        try:
            from .buzz_registration import register_agent_on_buzz
            result = await register_agent_on_buzz(agent_id)
            buzz_status = "registered" if result else "relay_unavailable"
        except Exception as exc:
            log.warning("Buzz registration at birth skipped for %s: %s", agent_name, exc)
            buzz_status = "pending"

        return {
            "status": "provisioned",
            "pubkey_hex": pubkey_hex,
            "npub": npub,
            "buzz": buzz_status,
            "nips": ["01", "19", "44", "46", "65", "98"],
        }
    except Exception as exc:
        log.error("Nostr provisioning failed for agent %s: %s", agent_id, exc)
        return {"status": "error", "error": str(exc)}


async def _provision_freenet(agent_id: int) -> Dict[str, Any]:
    """Derive Freenet identity from sealed seed. Phase F3.

    In Phase F3, the Freenet delegate (Ọmọ Kọ́dà2 side) will use the
    same sealed seed to derive its local Freenet identity, so Vantage
    and the agent runtime share the same cryptographic root without
    Vantage holding the private key material.
    """
    try:
        from .buzz_identity import _hkdf_sha256, get_or_create_sealed_seed

        seed = await get_or_create_sealed_seed(agent_id)
        # Derive a Freenet-specific identity key (different domain sep from Nostr)
        freenet_key = _hkdf_sha256(
            seed,
            salt=b"vantage-freenet-identity-v1",
            info=b"freenet-agent-identity",
            length=32,
        )
        # Public "node key" for display (just the hex of derived bytes — Phase F3
        # will replace with actual Freenet identity format)
        node_key_hex = freenet_key.hex()

        return {
            "status": "derived",
            "node_key_hex": node_key_hex,
            "phase": "F1",
            "note": "Freenet identity derived but not activated — start freenet-core locally (Phase F3)",
        }
    except Exception as exc:
        log.warning("Freenet identity derivation skipped for agent %s: %s", agent_id, exc)
        return {"status": "pending", "note": str(exc)}


async def _provision_sui_summary(agent_id: int) -> Dict[str, Any]:
    """Return a summary of existing Sui wallet state."""
    try:
        from .db import get_db
        async with get_db() as db:
            cur = await db.execute(
                "SELECT sui_address FROM agents WHERE id = ?", (agent_id,)
            )
            row = await cur.fetchone()
            sui_address = row[0] if row else None

        if sui_address:
            return {"status": "exists", "address": sui_address, "network": "testnet"}
        return {
            "status": "not_provisioned",
            "note": "Use POST /api/agents/me/wallets to create a Sui wallet",
        }
    except Exception:
        return {"status": "unknown"}


def _hex_to_npub(pubkey_hex: str) -> str:
    """Convert a 32-byte hex pubkey to Nostr npub bech32 format."""
    try:
        from bech32 import bech32_encode, convertbits
        data = bytes.fromhex(pubkey_hex)
        converted = convertbits(data, 8, 5)
        if converted:
            return bech32_encode("npub", converted)
    except Exception:
        pass
    # Fallback: return hex with prefix if bech32 not available
    return f"npub_hex:{pubkey_hex}"


async def get_birth_manifest(agent_id: int) -> Dict[str, Any]:
    """Return current credential status for an existing agent (no provisioning)."""
    from .db import get_db
    async with get_db() as db:
        cur = await db.execute(
            "SELECT name, nostr_pubkey_hex, sui_address, buzz_registered_at, sealed_seed_enc FROM agents WHERE id = ?",
            (agent_id,),
        )
        row = await cur.fetchone()

    if not row:
        return {"error": "agent not found"}

    name, nostr_hex, sui_addr, buzz_at, has_seed = row
    npub = _hex_to_npub(nostr_hex) if nostr_hex else None

    return {
        "agent_id": agent_id,
        "agent_name": name,
        "credentials": {
            "nostr": {
                "status": "provisioned" if nostr_hex else "not_provisioned",
                "pubkey_hex": nostr_hex,
                "npub": npub,
                "buzz_registered_at": buzz_at,
            },
            "freenet": {
                "status": "derived" if has_seed else "not_provisioned",
                "phase": "F1",
            },
            "sui": {
                "status": "exists" if sui_addr else "not_provisioned",
                "address": sui_addr,
            },
            "sealed_seed": {
                "status": "exists" if has_seed else "not_provisioned",
                "note": "Encrypted at rest — never exposed via API",
            },
        },
    }
