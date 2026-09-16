"""Multi-relay registry.

Reads from:
  NOSTR_RELAYS = "wss://relay1,wss://relay2"  (comma-separated, preferred)
  VANTAGE_RELAY_WS_URL                         (single relay, compat)
  BUZZ_RELAY_WS_URL                            (legacy compat)

Returns a list of relay URLs. First entry is the primary.
"""
import os
from typing import List

# Default from buzz_pairing (imported lazily to avoid circular import at top level)
_FALLBACK = "wss://omokoda.duckdns.org:3443"


def get_relay_urls() -> List[str]:
    """Return ordered list of relay URLs. First is primary."""
    multi = os.environ.get("NOSTR_RELAYS", "").strip()
    if multi:
        urls = [u.strip() for u in multi.split(",") if u.strip()]
        if urls:
            return urls

    single = (
        os.environ.get("VANTAGE_RELAY_WS_URL")
        or os.environ.get("BUZZ_RELAY_WS_URL")
        or _FALLBACK
    )
    return [single]


def primary_relay() -> str:
    """Convenience: return the single primary relay URL."""
    return get_relay_urls()[0]
