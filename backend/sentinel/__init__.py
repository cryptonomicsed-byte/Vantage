"""Ares Sentinel: the one home for security-relevant mutations.

P0 scope (this package today):
  * operators  — named, per-person instance roles (owner / security / operator),
                 bound to human sessions instead of the shared admin key.
  * signoffs   — the Human Input inbox for consequential actions: allow/deny
                 cards, non-blocking, requester can never approve their own.
  * live_controls — the only code that turns live trading on or arms a signer,
                 reached only from an approved, Èṣù-cleared sign-off.
"""
