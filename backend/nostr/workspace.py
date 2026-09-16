"""Guild workspace events.

Workspace events all ride on kind 9 (NIP-29 chat message) with
structured tags so a client that only speaks NIP-29 still renders
them as chat, while Vantage-aware clients can surface them as
structured workspace items.

Tag convention:
  ["h", group_id]           — NIP-29 group routing
  ["vt", "<type>"]          — vantage-type: claim | artifact | waggle
  ["vw", "<work_id>"]       — work-unit reference (for claim/artifact)
  ["resource", "<name>"]    — waggle resource name
  ["wkind", "<kind>"]       — waggle kind (compute | memory | attention | bandwidth)
  ["intensity", "<float>"]  — waggle intensity 0.0–1.0
"""
import logging
from typing import Optional

from .client import NostrSession

logger = logging.getLogger(__name__)


async def publish_task_claim(
    relay_url: str,
    agent_pk,
    group_id: str,
    task_id: str,
    task_desc: str,
) -> dict:
    """Publish a kind 9 task-claim event to the guild channel."""
    sess = NostrSession(relay_url, agent_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        tags = [
            ["h", group_id],
            ["vt", "claim"],
            ["vw", task_id],
        ]
        result = await sess.publish(9, task_desc, tags=tags)
        return {"ok": result["ack"][0] == "OK" and result["ack"][2], "event": result["event"]}
    except Exception as e:
        logger.warning("publish_task_claim failed: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def publish_artifact(
    relay_url: str,
    agent_pk,
    group_id: str,
    task_id: str,
    artifact_url: str,
    summary: str,
) -> dict:
    """Publish a kind 9 artifact-delivery event to the guild channel."""
    sess = NostrSession(relay_url, agent_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        content = summary
        if artifact_url:
            content = f"{summary}\n\nArtifact: {artifact_url}" if summary else artifact_url
        tags = [
            ["h", group_id],
            ["vt", "artifact"],
            ["vw", task_id],
            ["url", artifact_url],
        ]
        result = await sess.publish(9, content, tags=tags)
        return {"ok": result["ack"][0] == "OK" and result["ack"][2], "event": result["event"]}
    except Exception as e:
        logger.warning("publish_artifact failed: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def publish_status(
    relay_url: str,
    agent_pk,
    group_id: str,
    status: str,
    context: str = "",
) -> dict:
    """Publish kind 30315 (NIP-38 user_status) with group context tag."""
    sess = NostrSession(relay_url, agent_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        tags = [
            ["d", "general"],
            ["h", group_id],
        ]
        content = status
        if context:
            content = f"{status}: {context}"
        result = await sess.publish(30315, content, tags=tags)
        return {"ok": result["ack"][0] == "OK" and result["ack"][2], "event": result["event"]}
    except Exception as e:
        logger.warning("publish_status failed: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def publish_waggle_signal(
    relay_url: str,
    agent_pk,
    group_id: str,
    resource: str,
    kind: str,
    intensity: float,
    note: str = "",
) -> dict:
    """Publish a waggle-field signal into the guild as a kind 9 event.

    Bridges waggle field signals into guild visibility so all agents
    in the guild can see resource demands and coordination signals.
    """
    sess = NostrSession(relay_url, agent_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        tags = [
            ["h", group_id],
            ["vt", "waggle"],
            ["resource", resource],
            ["wkind", kind],
            ["intensity", str(round(float(intensity), 4))],
        ]
        content = note or f"waggle: {resource} {kind} intensity={intensity:.3f}"
        result = await sess.publish(9, content, tags=tags)
        return {"ok": result["ack"][0] == "OK" and result["ack"][2], "event": result["event"]}
    except Exception as e:
        logger.warning("publish_waggle_signal failed: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def fetch_workspace_feed(
    relay_url: str,
    admin_pk,
    group_id: str,
    since: int = 0,
    limit: int = 50,
    vt_filter: Optional[str] = None,
) -> list:
    """Fetch recent workspace events (claims, artifacts, waggles) from the guild.

    Returns a list of NIP-01 event dicts in reverse-chronological order.
    """
    sess = NostrSession(relay_url, admin_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        f: dict = {"kinds": [9, 30315], "#h": [group_id], "limit": limit}
        if since:
            f["since"] = since
        sub_id = await sess.subscribe([f])
        events = await sess.recv_until_eose(sub_id, max_events=limit)
        if vt_filter:
            def _has_vt(ev):
                for tag in ev.get("tags", []):
                    if tag and tag[0] == "vt" and len(tag) >= 2 and tag[1] == vt_filter:
                        return True
                return False
            events = [e for e in events if _has_vt(e)]
        events.sort(key=lambda e: e.get("created_at", 0), reverse=True)
        return events
    except Exception as e:
        logger.warning("fetch_workspace_feed failed for %s: %s", group_id, e)
        return []
    finally:
        await sess.close()
