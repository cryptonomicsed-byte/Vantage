"""nostr/ — relay-agnostic Nostr client layer for Vantage.

Public surface:
  nostr.client     — NostrSession, build_event, BuzzSession (alias)
  nostr.identity   — keypair derivation, NOSTR_HKDF_INFO/SALT aliases
  nostr.kinds      — kind registry (re-exports nostr_kinds)
  nostr.registry   — multi-relay URL list
  nostr.groups     — NIP-29 group management over WS (no docker)
  nostr.adapters   — RelayAdapter protocol + Nip29/Buzz/Generic impls
  nostr.workspace  — workspace events (claims, artifacts, status, waggle)
"""
from .client import NostrSession, BuzzSession, build_event
from .registry import get_relay_urls, primary_relay
from . import groups

__all__ = [
    "NostrSession",
    "BuzzSession",
    "build_event",
    "get_relay_urls",
    "primary_relay",
    "groups",
]
