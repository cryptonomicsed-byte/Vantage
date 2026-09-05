"""The event-kind registry for this ecosystem.

`omokoda-mesh/docs/EVENT_KINDS.md` records, under "Open items", that no
ecosystem-wide kind registry exists -- the repositories "just avoid
collisions ad hoc". That is fine while there are two of them and it stops
being fine at five. Vantage is where the relay, the guilds, the indexer and
the mesh gateway all meet, so the registry lives here and the other
repositories read it.

Three rules this file exists to enforce:

1. **Never mint a kind for a concept that already has one.** Most entries
   below are `Origin.NIP` or `Origin.ECOSYSTEM`; the short custom list is
   the exception and each one carries a reason.
2. **A number appears exactly once.** `test_nostr_kinds.py` fails the build
   on a collision, which is the whole point of centralising this.
3. **Provisional is not locked.** A kind with `locked=False` may still move.
   Callers that persist it should record the number they wrote, not assume
   the constant is stable forever.

Nothing here changes behaviour on import. Existing modules keep their own
`KIND_* = <n>` constants; `test_nostr_kinds.py` asserts those agree with
this table rather than requiring ~25 modules to be rewritten at once.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Origin(str, Enum):
    """Where a kind's authority comes from -- which decides who may change it."""

    NIP = "nip"                # A published NIP. Not ours to change.
    ECOSYSTEM = "ecosystem"    # Locked in a sibling repository's schema doc.
    VANTAGE = "vantage"        # Minted here. Ours to change, with care.


@dataclass(frozen=True)
class KindSpec:
    kind: int
    name: str
    origin: Origin
    summary: str
    #: The NIP number, or the repo/path that locks it.
    authority: str = ""
    #: False means the number itself may still move.
    locked: bool = True
    #: Set when this kind was considered and deliberately not minted.
    instead_of: Optional[str] = None


# ── Nostr proper ─────────────────────────────────────────────────────────────
# Kinds defined by a published NIP. Vantage implements them; it does not own
# them, and a disagreement with the NIP is a Vantage bug.

_NIP_KINDS = [
    KindSpec(9, "message", Origin.NIP, "Chat message. The guild/workspace log.", "NIP-29"),
    KindSpec(9007, "create_channel", Origin.NIP, "Create a relay-based group.", "NIP-29"),
    KindSpec(9021, "join_request", Origin.NIP, "Self-join an open group.", "NIP-29"),
    KindSpec(9040, "moderation_ban", Origin.NIP, "Remove a member from a group.", "NIP-29"),
    KindSpec(9041, "moderation_unban", Origin.NIP, "Restore a removed member.", "NIP-29"),
    KindSpec(22242, "client_auth", Origin.NIP, "Relay authentication challenge response.", "NIP-42"),
    KindSpec(24133, "nostr_connect", Origin.NIP, "Remote signing session.", "NIP-46"),
    KindSpec(30315, "user_status", Origin.NIP, "Live status of a principal.", "NIP-38"),
    KindSpec(30617, "git_repo_announcement", Origin.NIP, "Repository announcement.", "NIP-34"),
    KindSpec(40100, "set_canvas", Origin.NIP, "Channel canvas state.", "NIP-28 family"),
]

# ── Locked elsewhere in the ecosystem ────────────────────────────────────────
# These belong to sibling repositories. Vantage consumes and produces them but
# must not redefine their shape; re-read the named schema before changing a
# consumer.

_ECOSYSTEM_KINDS = [
    KindSpec(
        1901, "creation_receipt", Origin.ECOSYSTEM,
        "Proof that a creation happened, linking back to an IP root.",
        "ip-layer/schemas/creation_receipt.md",
    ),
    KindSpec(
        1902, "attestation", Origin.ECOSYSTEM,
        "vouch | challenge | confirm on a subject event, with optional stake weight. "
        "Attestor-agnostic by design: a human, a peer agent, a mesh relay node and a "
        "runtime receipt all use this one shape.",
        "ip-layer/schemas/attestation.md",
    ),
    KindSpec(
        1903, "twin_binding", Origin.ECOSYSTEM,
        "Agent-to-device embodiment pairing. Parameterized replaceable.",
        "ip-layer/schemas/twin_binding.md",
    ),
    KindSpec(
        30174, "agent_engram", Origin.ECOSYSTEM,
        "Portable agent memory. Vantage's KIND_ENGRAM is this kind.",
        "minipae (NIP-AE)",
    ),
    KindSpec(
        31900, "ip_root", Origin.ECOSYSTEM,
        "Root of an intellectual-property lineage.",
        "ip-layer/schemas/ip_root.md",
    ),
    # Mesh. Provisional in their own repo, and recorded as provisional here --
    # this registry is the place a formal reservation would eventually happen.
    KindSpec(
        20000, "mesh_packet_route", Origin.ECOSYSTEM,
        "A relayed mesh packet with per-hop signatures appended.",
        "omokoda-mesh/docs/EVENT_KINDS.md", locked=False,
    ),
    KindSpec(
        20001, "mesh_telemetry", Origin.ECOSYSTEM,
        "Location or sensor telemetry from a mesh or bridged node.",
        "omokoda-mesh/docs/EVENT_KINDS.md", locked=False,
    ),
    KindSpec(
        20003, "mesh_node_presence", Origin.ECOSYSTEM,
        "Mesh node capability, radio and battery announcement.",
        "omokoda-mesh/docs/EVENT_KINDS.md", locked=False,
    ),
]

# ── Minted by Vantage ────────────────────────────────────────────────────────
# Everything Vantage itself introduced. Kept short on purpose: the coordination
# layer's whole structure (guild, channel, message type, work reference) rides
# as tags on kind 9 rather than as new kinds, so a client that understands
# nothing but chat still renders a guild correctly.

_VANTAGE_KINDS = [
    KindSpec(24134, "pairing", Origin.VANTAGE, "Device/agent pairing handshake.", "buzz_pairing.py"),
    KindSpec(24200, "observer_frame", Origin.VANTAGE, "Observation frame from a watching agent.", "buzz_observer.py"),
    KindSpec(30175, "persona", Origin.VANTAGE, "Agent persona definition.", "buzz_persona.py"),
    KindSpec(30176, "team", Origin.VANTAGE, "Team definition.", "buzz_client.py"),
    KindSpec(30177, "managed_agent", Origin.VANTAGE, "Hosted-agent definition.", "buzz_managed_agent.py"),
    KindSpec(30620, "workflow_def", Origin.VANTAGE, "Workflow definition.", "buzz_workflows.py"),
    KindSpec(41010, "dm_open", Origin.VANTAGE, "Open a direct-message thread.", "buzz_dm.py"),
    KindSpec(44100, "member_added", Origin.VANTAGE, "Member-added notification.", "buzz_rooms.py"),
    KindSpec(46020, "workflow_trigger", Origin.VANTAGE, "Fire a defined workflow.", "buzz_workflows.py"),
]

# ── Considered and deliberately not minted ───────────────────────────────────
# The value of a registry is as much in what it refuses. Each of these was a
# plausible new kind; each is served by an existing one. Recorded so the same
# proposal does not come back a third time.

NOT_MINTED = [
    KindSpec(
        0, "work_claim", Origin.VANTAGE,
        "Claiming a unit of work.", instead_of="kind 9 with vt=claim and a vw work reference",
    ),
    KindSpec(
        0, "work_artifact", Origin.VANTAGE,
        "Delivering a unit of work.", instead_of="kind 9 with vt=artifact and a vw work reference",
    ),
    KindSpec(
        0, "runtime_receipt", Origin.VANTAGE,
        "A runtime's signed proof that it really executed an action.",
        instead_of="kind 1902 attestation, stance=confirm, e-tagging the artifact event",
    ),
    KindSpec(
        0, "agent_presence", Origin.VANTAGE,
        "Agent working state (available / thinking / working / blocked).",
        instead_of="kind 30315 user status, which NIP-38 already defines for exactly this",
    ),
    KindSpec(
        0, "freenet_contract_state", Origin.VANTAGE,
        "State of a Freenet contract backing a channel.",
        instead_of="the transport carries no kind of its own -- it moves kind 9 events unchanged",
    ),
]

REGISTRY: dict[int, KindSpec] = {}
for _spec in _NIP_KINDS + _ECOSYSTEM_KINDS + _VANTAGE_KINDS:
    if _spec.kind in REGISTRY:
        raise RuntimeError(
            f"kind {_spec.kind} registered twice: "
            f"{REGISTRY[_spec.kind].name} and {_spec.name}"
        )
    REGISTRY[_spec.kind] = _spec
del _spec

BY_NAME: dict[str, KindSpec] = {s.name: s for s in REGISTRY.values()}


def kind(name: str) -> int:
    """Look a kind number up by name. Raises rather than returning a default,
    because publishing to kind 0 would be silently wrong."""
    try:
        return BY_NAME[name].kind
    except KeyError:
        raise KeyError(f"unknown event kind name: {name!r}") from None


def describe(number: int) -> Optional[KindSpec]:
    return REGISTRY.get(number)


def is_ours(number: int) -> bool:
    """True if Vantage may change this kind's shape unilaterally."""
    spec = REGISTRY.get(number)
    return spec is not None and spec.origin is Origin.VANTAGE
