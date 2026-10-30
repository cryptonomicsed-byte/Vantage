"""Rust steward client — BIP-340 Schnorr verification via ares-steward."""
import os
import httpx

STEWARD_URL = os.environ.get("STEWARD_URL", "http://localhost:7702")


async def verify_schnorr(pubkey_hex: str, message_hex: str, sig_hex: str) -> bool:
    """Verify a BIP-340 Schnorr signature via Rust steward.
    Falls back to local coincurve on error."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.post(
                f"{STEWARD_URL}/verify",
                json={"pubkey_hex": pubkey_hex, "message_hex": message_hex, "sig_hex": sig_hex},
            )
            return r.json().get("valid", False)
    except Exception:
        # Fallback to coincurve if steward unreachable
        try:
            from coincurve import PublicKeyXOnly
            pk = PublicKeyXOnly(bytes.fromhex(pubkey_hex))
            return pk.verify(bytes.fromhex(sig_hex), bytes.fromhex(message_hex))
        except Exception:
            return False
