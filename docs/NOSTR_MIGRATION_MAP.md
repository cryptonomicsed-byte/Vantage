# Nostr migration map

State as of commit `4e18eab`. The goal: `nostr/` owns the transport, Buzz is one
adapter behind it, and any NIP-01 client can participate. Target kinds are
standard NIP-29 (9007/9000/9001/9002, 39000-39002), not a Buzz invention.

## The finding

`nostr/` was built and almost nothing used it. `nostr/client.py` is a rename of
`buzz_client.py` with a `BuzzSession` alias for compatibility, but `buzz_client.py`
was left in place and untouched, so ~53 `BuzzSession(...)` call sites across the
codebase still imported the old path. The abstraction existed; the adoption did not.

Applied at `4e18eab`:

- `buzz_client.py` is now a 3-line re-export shim of `nostr.client`. All 53 call
  sites keep working, proven by identity: `buzz_client.BuzzSession is
  nostr.client.BuzzSession == True`. New code imports `nostr.client`.
- `docker-exec` (the non-portable part — it only works on a box where Vantage owns
  the relay container) now exists in exactly **one** file, `nostr/adapters/buzz.py`.
  `buzz_inbound.py` and `buzz_human_identity.py` each carried their own copy; they
  now import the adapter's. Behaviour unchanged.

## Module classification

Legend — **transport**: should call `nostr/client.py`, keeps its logic. **adapter**:
Buzz-specific machinery that belongs behind `nostr/adapters/buzz.py`. **keys**:
protocol-agnostic already, badly named only.

| module | LOC | class | note |
|---|---:|---|---|
| `buzz_client.py` | 3 | done | now a shim of `nostr.client` |
| `buzz_registration.py` | 161 | done | already imports `nostr.adapters.buzz` + `nostr.groups` |
| `buzz_guild_provisioning.py` | 66 | adapter | `/operator/communities` (Buzz-only provisioning) |
| `buzz_human_identity.py` | 190 | adapter | 6 docker-exec sites -> `nostr.groups.*` |
| `buzz_inbound.py` | 395 | adapter | 2 docker-exec sites -> `nostr.groups.*` |
| `buzz_identity.py` | 337 | keys | pure key derivation, zero transport use; `nostr/identity.py` already aliases it |
| `buzz_pairing.py` | 420 | transport | largest; uses `PublicKey.multiply` ECDH |
| `buzz_nip46.py` | 274 | transport | NIP-46 remote signer |
| `buzz_bridge.py` | 250 | transport | feed publishing |
| `buzz_workflows.py` | 204 | transport | |
| `buzz_persona.py` | 191 | transport | |
| `buzz_engram.py` | 140 | transport | |
| `buzz_engrams.py` | 106 | transport | |
| `buzz_observer.py` | 109 | transport | reads events |
| `buzz_rooms.py` | 125 | transport | |
| `buzz_dm.py` | 122 | transport | |
| `buzz_genesis.py` | 83 | transport | |
| `buzz_trading_channel.py` | 70 | transport | |
| `buzz_moderation.py` | 69 | transport | |
| `buzz_managed_agent.py` | 69 | transport | |
| `buzz_ops_channel.py` | 67 | transport | |
| `buzz_feed.py` | 61 | transport | |
| `buzz_git.py` | 45 | transport | |
| `buzz_status.py` | 28 | transport | |
| `buzz_acp_bridge.py` | 97 | transport | |

22 of 25 modules are **transport** — they hold domain logic and happen to use
`BuzzSession`. The shim already points them at `nostr/`. Renaming them
(`buzz_dm` -> `nostr/dm`) is cosmetic and should come last.

## Remaining work, in order

1. **`/join`-style membership without docker.** `buzz_human_identity.py` (6 sites)
   and `buzz_inbound.py` (2 sites) still shell into the relay container to grant
   membership. The portable equivalent already exists: `nostr/groups.add_member`
   publishes a kind-9000 event signed by the group owner. This is the change that
   makes Vantage work against a relay it does not host — damus, nos.lol, a friend's
   relay. **Needs a live relay to verify; do not land it blind.**
2. **Provisioning.** `buzz_guild_provisioning.py`'s `POST /operator/communities` is
   Buzz-only and operator-gated. Behind the adapter until NIP-29 group creation is
   the only path.
3. **Capability degradation.** `nostr/adapters/__init__.py` currently enumerates
   `GROUPS / AUTH_NIP42 / SEARCH_NIP50 / PROVISION` with no fallback ladder. Add
   NIP-29 -> NIP-28 (kind 40/42) -> kind 1 + tags so a relay that is not NIP-29
   still works.
4. **Relay discovery.** `nostr/registry.py` (35 lines) reads a static
   `NOSTR_RELAYS` list. NIP-65 outbound relay lists are published
   (`agents.py:798`, kind 10002) and never read. Reading peers' lists is what makes
   federation real.
5. **Rename the transport modules** last, one commit, purely cosmetic.

## What is NOT in scope here

`nostr/adapters/buzz.py` is the correct home for docker-exec and the operator API,
and it is now the only one. But the portability goal is met by *replacing* those
calls, not by confining them — confinement just stops the spread.
