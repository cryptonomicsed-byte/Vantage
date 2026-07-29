"""Real Buzz Workflows/Automations integration -- Nostr protocol, not a
REST proxy. Buzz's own workflow engine has no REST API; workflows are
authored/listed/triggered/deleted purely via signed Nostr events against
the relay, exactly as buzz-cli's commands/workflows.rs does it. This
module is Vantage's equivalent of that CLI, reusing the existing
buzz_client.py/buzz_identity.py building blocks (same identity + relay
already used by buzz_registration.py).

Event kinds (from buzz-relay/crates/buzz-core/src/kind.rs, the
authoritative registry):
  30620 - workflow definition (NIP-33 parameterized-replaceable, d=workflow id)
  46020 - workflow trigger (content = JSON inputs or {})
  5     - NIP-09 deletion (e-tag referencing the definition event id)

Execution/run history (kinds 46001-46006) and approval events are
DELIBERATELY NOT implemented here:
  - buzz-cli's own cmd_get_workflow_runs() documents that the relay does
    not yet emit 46001-46003 execution events at all -- querying for them
    always returns empty regardless of what a client does. Building a
    "run history" UI against this would show fake/always-empty data, so
    it's left out until the relay actually emits these.
  - The approval kind numbers are inconsistent between kind.rs (46011
    grant / 46012 deny) and buzz-cli's cmd_approve_step (46030/46031
    literals) -- unresolved upstream discrepancy, so approval handling is
    out of scope until Buzz reconciles it.
"""
import json
import time
import uuid
from typing import Optional

from .buzz_client import BuzzSession, build_event
from .buzz_identity import derive_buzz_keypair, public_key_xonly_hex
from .buzz_registration import RELAY_WS_URL, DEFAULT_CHANNEL_ID

KIND_WORKFLOW_DEF = 30620
KIND_WORKFLOW_TRIGGER = 46020

_VALID_TRIGGER_TYPES = {"message_posted", "reaction_added", "diff_posted", "schedule", "webhook"}
_VALID_ACTION_TYPES = {
    "send_message", "send_dm", "set_channel_topic", "add_reaction",
    "call_webhook", "request_approval", "delay",
}


def validate_workflow_def(definition: dict) -> None:
    """Mirrors buzz-workflow's schema.rs WorkflowDef::validate() rules,
    checked client-side so authors get an immediate, specific error
    instead of a silent relay-side rejection."""
    if not definition.get("name", "").strip():
        raise ValueError("name is required and cannot be empty")

    trigger = definition.get("trigger")
    if not isinstance(trigger, dict) or trigger.get("on") not in _VALID_TRIGGER_TYPES:
        raise ValueError(f"trigger.on must be one of {sorted(_VALID_TRIGGER_TYPES)}")
    if trigger["on"] == "schedule":
        has_cron = bool(trigger.get("cron"))
        has_interval = bool(trigger.get("interval"))
        if has_cron == has_interval:
            raise ValueError("schedule trigger requires exactly one of cron or interval")
        if has_interval:
            interval = trigger["interval"]
            secs = _parse_duration_secs(interval)
            if secs < 60:
                raise ValueError("schedule interval must be >= 60s (cron loop ticks every 60s)")

    steps = definition.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        raise ValueError("at least one step is required")
    seen_ids = set()
    for step in steps:
        step_id = step.get("id", "")
        if not step_id or not all(c.isalnum() or c == "_" for c in step_id) or len(step_id) > 64:
            raise ValueError(f"invalid step id {step_id!r}: must be alphanumeric+underscore, <=64 chars")
        if step_id in seen_ids:
            raise ValueError(f"duplicate step id {step_id!r}")
        seen_ids.add(step_id)
        action = step.get("action")
        if action not in _VALID_ACTION_TYPES:
            raise ValueError(f"step {step_id!r}: action must be one of {sorted(_VALID_ACTION_TYPES)}")


def _parse_duration_secs(spec: str) -> int:
    """Parses "90s"/"5m"/"2h"-style durations used by interval/delay."""
    spec = spec.strip()
    units = {"s": 1, "m": 60, "h": 3600}
    unit = spec[-1]
    if unit not in units:
        raise ValueError(f"invalid duration {spec!r}: must end in s/m/h")
    return int(spec[:-1]) * units[unit]


async def _session_for(agent_id: int) -> tuple[BuzzSession, str]:
    pk = await derive_buzz_keypair(agent_id)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    return sess, public_key_xonly_hex(pk)


async def create_workflow(agent_id: int, definition: dict, channel_id: str = DEFAULT_CHANNEL_ID,
                           workflow_id: Optional[str] = None) -> dict:
    validate_workflow_def(definition)
    workflow_id = workflow_id or uuid.uuid4().hex
    definition.setdefault("enabled", True)
    sess, pubkey = await _session_for(agent_id)
    try:
        result = await sess.publish(
            KIND_WORKFLOW_DEF,
            json.dumps(definition),
            tags=[["d", workflow_id], ["h", channel_id]],
        )
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected workflow def: {result['ack']}")
    return {"ok": True, "workflow_id": workflow_id, "pubkey": pubkey, "event": result["event"]}


async def update_workflow(agent_id: int, workflow_id: str, definition: dict,
                           channel_id: str = DEFAULT_CHANNEL_ID) -> dict:
    """Same d-tag as create -- NIP-33 replaceable event overwrites the prior def."""
    return await create_workflow(agent_id, definition, channel_id, workflow_id)


async def _query(agent_id: int, filt: dict, max_events: int = 200) -> list[dict]:
    sess, _ = await _session_for(agent_id)
    try:
        sub_id = await sess.subscribe([filt])
        return await sess.recv_until_eose(sub_id, max_events=max_events)
    finally:
        await sess.close()


def _latest_by_d_tag(events: list[dict]) -> dict[str, dict]:
    """Parameterized-replaceable events: keep only the newest per d-tag,
    since the relay may return older definitions/revisions on query."""
    latest: dict[str, dict] = {}
    for ev in events:
        d = next((t[1] for t in ev.get("tags", []) if t[0] == "d"), None)
        if d is None:
            continue
        if d not in latest or ev["created_at"] > latest[d]["created_at"]:
            latest[d] = ev
    return latest


def _parse_workflow_event(ev: dict) -> dict:
    d = next((t[1] for t in ev.get("tags", []) if t[0] == "d"), None)
    try:
        definition = json.loads(ev["content"])
    except (json.JSONDecodeError, TypeError):
        definition = None
    return {
        "workflow_id": d,
        "definition": definition,
        "event_id": ev["id"],
        "pubkey": ev["pubkey"],
        "created_at": ev["created_at"],
    }


async def list_workflows(agent_id: int, channel_id: str = DEFAULT_CHANNEL_ID) -> list[dict]:
    events = await _query(agent_id, {"kinds": [KIND_WORKFLOW_DEF], "#h": [channel_id]})
    latest = _latest_by_d_tag(events)
    return [_parse_workflow_event(ev) for ev in latest.values()]


async def get_workflow(agent_id: int, workflow_id: str) -> Optional[dict]:
    events = await _query(agent_id, {"kinds": [KIND_WORKFLOW_DEF], "#d": [workflow_id]}, max_events=20)
    if not events:
        return None
    latest = max(events, key=lambda e: e["created_at"])
    return _parse_workflow_event(latest)


async def trigger_workflow(agent_id: int, workflow_id: str, inputs: Optional[dict] = None) -> dict:
    sess, pubkey = await _session_for(agent_id)
    try:
        result = await sess.publish(
            KIND_WORKFLOW_TRIGGER,
            json.dumps(inputs or {}),
            tags=[["d", workflow_id]],
        )
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected trigger: {result['ack']}")
    return {"ok": True, "workflow_id": workflow_id, "pubkey": pubkey, "event": result["event"]}


async def delete_workflow(agent_id: int, workflow_id: str) -> dict:
    existing = await get_workflow(agent_id, workflow_id)
    if existing is None:
        raise ValueError(f"no workflow found with id {workflow_id!r}")
    sess, pubkey = await _session_for(agent_id)
    try:
        result = await sess.publish(5, "", tags=[["e", existing["event_id"]]])
    finally:
        await sess.close()
    if not result["ack"][2]:
        raise RuntimeError(f"relay rejected deletion: {result['ack']}")
    return {"ok": True, "workflow_id": workflow_id, "pubkey": pubkey}
