"""Buzz Persona/Team catalog sync (NIP-AP) -- publishes a Vantage agent's
existing profile as a real, replaceable kind:30175 Persona event, and a
guild as a kind:30176 Team event. ADDON, not a replacement: Vantage's own
agent/guild DB rows stay the single source of truth; these are one-way
publications derived from them. Turning this off (never calling
publish_persona) removes zero Vantage capability.

Kinds (buzz-relay/crates/buzz-core/src/kind.rs):
  30175 - Agent Persona (parameterized replaceable, d=persona slug)
  30176 - Agent Team (parameterized replaceable, d=team id)

Read-gating (kind.rs's own doc comment, load-bearing -- get this wrong and
system prompts leak community-wide):
  - No `["shared","true"]` tag (exactly two elements, value "true") =
    author-only. This is the DEFAULT for every published persona.
  - Exactly one `["shared","true"]` tag = world-readable catalog entry.
  - Any other shape (extra elements, wrong value, >1 shared tag) is
    rejected by the relay at ingest and MUST NOT be sent.

Publishing is always an explicit, opt-in action from this module's callers
-- nothing here runs automatically, matching the session's established
"agent-first, additive, kill-switchable" pattern.
"""
import json
import re
from typing import Optional

from .buzz_client import BuzzSession, build_event
from .buzz_identity import derive_buzz_keypair
from .buzz_registration import RELAY_WS_URL

KIND_PERSONA = 30175
KIND_TEAM = 30176

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]", "-", name.lower()).strip("-")[:64]
    return slug or "agent"


async def _publish(agent_id: int, kind: int, content: str, tags: list) -> dict:
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        result = await sess.publish(kind, content, tags=tags)
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected event: {result['ack']}")
    return result


async def publish_persona(
    agent_id: int,
    *,
    display_name: str,
    system_prompt: str = "",
    avatar_url: str = "",
    runtime: str = "vantage",
    model: Optional[str] = None,
    provider: Optional[str] = None,
    shared: bool = False,
    slug: Optional[str] = None,
) -> dict:
    """Publish (or update -- same d-tag replaces) this agent's Persona.

    `shared=False` (the default) keeps it author-only: only this agent's
    own key can read it back. Only pass shared=True when the owner has
    explicitly opted this agent into the public catalog -- there is no
    implicit default-on path anywhere in this module.
    """
    persona_slug = slug or _slugify(display_name)
    if not _SLUG_RE.match(persona_slug):
        raise ValueError(f"invalid persona slug {persona_slug!r}")

    body = {
        "display_name": display_name,
        "system_prompt": system_prompt,
        "avatar_url": avatar_url,
        "runtime": runtime,
    }
    if model:
        body["model"] = model
    if provider:
        body["provider"] = provider

    tags = [["d", persona_slug]]
    if shared:
        tags.append(["shared", "true"])

    result = await _publish(agent_id, KIND_PERSONA, json.dumps(body), tags)
    return {"ok": True, "slug": persona_slug, "shared": shared, "event": result["event"]}


async def publish_team(
    agent_id: int,
    *,
    team_id: str,
    name: str,
    description: str = "",
    persona_ids: Optional[list] = None,
) -> dict:
    """Publish a guild as a Team catalog entry. `persona_ids` are the
    member agents' own persona slugs (their kind:30175 d-tags), NOT
    pubkeys -- lets a Team reference personas regardless of whether they
    are individually shared."""
    if not _SLUG_RE.match(team_id):
        raise ValueError(f"invalid team id {team_id!r}")

    body = {
        "name": name,
        "description": description,
        "persona_ids": persona_ids or [],
    }
    result = await _publish(agent_id, KIND_TEAM, json.dumps(body), [["d", team_id]])
    return {"ok": True, "team_id": team_id, "event": result["event"]}


async def get_own_persona(agent_id: int) -> Optional[dict]:
    """Read back this agent's own persona (author-only reads always
    succeed regardless of the shared flag, per kind.rs's own read model)."""
    pk = await derive_buzz_keypair(agent_id)
    from .buzz_identity import public_key_xonly_hex
    pubkey = public_key_xonly_hex(pk)

    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        sub_id = await sess.subscribe([{"kinds": [KIND_PERSONA], "authors": [pubkey]}])
        events = await sess.recv_until_eose(sub_id, max_events=20)
    finally:
        await sess.close()
    if not events:
        return None
    latest = max(events, key=lambda e: e["created_at"])
    d_tag = next((t[1] for t in latest.get("tags", []) if t[0] == "d"), None)
    shared_tag = any(t == ["shared", "true"] for t in latest.get("tags", []))
    return {
        "slug": d_tag,
        "shared": shared_tag,
        "body": json.loads(latest["content"]),
        "created_at": latest["created_at"],
    }


async def browse_public_personas(agent_id: int, limit: int = 100) -> list:
    """List the community-wide public persona catalog -- every kind:30175
    event carrying exactly `["shared","true"]`, from any author. The relay
    itself withholds unshared events from non-author readers (kind.rs's
    is_unshared_persona_event gate), so this REQ only ever sees what's
    genuinely public -- no client-side filtering needed for correctness,
    though we double-check the tag defensively below anyway."""
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    try:
        sub_id = await sess.subscribe([{"kinds": [KIND_PERSONA], "limit": limit}])
        events = await sess.recv_until_eose(sub_id, max_events=limit)
    finally:
        await sess.close()

    latest_by_author_slug: dict[tuple, dict] = {}
    for ev in events:
        if not any(t == ["shared", "true"] for t in ev.get("tags", [])):
            continue
        d_tag = next((t[1] for t in ev.get("tags", []) if t[0] == "d"), None)
        key = (ev["pubkey"], d_tag)
        if key not in latest_by_author_slug or ev["created_at"] > latest_by_author_slug[key]["created_at"]:
            latest_by_author_slug[key] = ev

    out = []
    for ev in latest_by_author_slug.values():
        try:
            body = json.loads(ev["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        d_tag = next((t[1] for t in ev.get("tags", []) if t[0] == "d"), None)
        out.append({
            "pubkey": ev["pubkey"],
            "slug": d_tag,
            "created_at": ev["created_at"],
            **body,
        })
    return out
