"""Backward-compat shim — the Nostr transport now lives in `nostr/client.py`.

This module used to BE the transport. A previous pass created `nostr/client.py`
with an identical implementation and a `BuzzSession` alias, but left this file in
place and untouched, so all ~53 `BuzzSession(...)` call sites across the codebase
still imported from here: the abstraction existed and nothing used it. Buzz was
still the architecture in everything but name.

Re-exporting rather than rewriting 53 call sites flips the dependency direction in
one edit, with no behaviour change:

    before:  agents, buzz_dm, buzz_feed, ...  ->  buzz_client.BuzzSession
    after:   agents, buzz_dm, buzz_feed, ...  ->  buzz_client (shim)
                                                 -> nostr.client.BuzzSession

`nostr/` now owns the transport, `nostr/adapters/buzz.py` is the only place that
knows how to shell into the relay container, and Buzz is demoted to one adapter —
which is the stated design principle. New code should import `nostr.client`
directly; this shim exists only so nothing has to move at once.
"""
from .nostr.client import BuzzSession, NostrSession, build_event, _event_id

__all__ = [
    "NostrSession",
    "BuzzSession",
    "build_event",
]
