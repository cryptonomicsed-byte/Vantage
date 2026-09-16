"""NIP-29 group management over WebSocket — no docker-exec, no operator API.

All functions open a fresh session, perform the action, and close. For
long-lived sessions the caller should use NostrSession directly.

NIP-29 kind reference:
  9007  create group (group owner publishes)
  9000  put-user / add-user (admin publishes; adds a member)
  9001  remove-user (admin publishes)
  9021  join-request (member publishes; for open groups)
  9022  leave-request (member publishes)
"""
import logging
import uuid
from typing import Optional

from .client import NostrSession

logger = logging.getLogger(__name__)


async def provision_group(
    relay_ws_url: str,
    owner_pk,
    group_id: str,
    name: str,
    about: str = "",
    picture: str = "",
) -> dict:
    """Publish kind 9007 (create group) signed by owner_pk.

    Returns {"ok": True, "group_id": group_id, "event_id": ...} or
            {"ok": False, "error": ...}.
    """
    sess = NostrSession(relay_ws_url, owner_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        tags = [
            ["h", group_id],
            ["name", name],
        ]
        if about:
            tags.append(["about", about])
        if picture:
            tags.append(["picture", picture])
        result = await sess.publish(9007, "", tags=tags)
        ack = result["ack"]
        if ack[0] == "OK" and ack[2]:
            return {"ok": True, "group_id": group_id, "event_id": result["event"]["id"]}
        return {"ok": False, "error": str(ack)}
    except Exception as e:
        logger.warning("provision_group failed for %s: %s", group_id, e)
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def add_member(
    relay_ws_url: str,
    admin_pk,
    group_id: str,
    target_pubkey: str,
    role: str = "member",
) -> dict:
    """Publish kind 9000 (put-user) signed by admin_pk to add target_pubkey.

    This is the NIP-29 replacement for _docker_exec("add-member", ...).
    Returns {"ok": True, "event_id": ...} or {"ok": False, "error": ...}.
    """
    sess = NostrSession(relay_ws_url, admin_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        tags = [
            ["h", group_id],
            ["p", target_pubkey, role],
        ]
        result = await sess.publish(9000, "", tags=tags)
        ack = result["ack"]
        if ack[0] == "OK" and ack[2]:
            return {"ok": True, "event_id": result["event"]["id"]}
        # Relay may return ok=false with "already a member" — treat as success
        msg = str(ack).lower()
        if "already" in msg or "exists" in msg:
            return {"ok": True, "event_id": result["event"]["id"], "note": "already member"}
        return {"ok": False, "error": str(ack)}
    except Exception as e:
        logger.warning("add_member failed for %s in %s: %s", target_pubkey[:8], group_id, e)
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def remove_member(
    relay_ws_url: str,
    admin_pk,
    group_id: str,
    target_pubkey: str,
) -> dict:
    """Publish kind 9001 (remove-user) signed by admin_pk."""
    sess = NostrSession(relay_ws_url, admin_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        tags = [["h", group_id], ["p", target_pubkey]]
        result = await sess.publish(9001, "", tags=tags)
        ack = result["ack"]
        if ack[0] == "OK" and ack[2]:
            return {"ok": True, "event_id": result["event"]["id"]}
        return {"ok": False, "error": str(ack)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def join_group(
    relay_ws_url: str,
    member_pk,
    group_id: str,
    message: str = "",
) -> dict:
    """Publish kind 9021 (join request) — for open groups."""
    sess = NostrSession(relay_ws_url, member_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        tags = [["h", group_id]]
        result = await sess.publish(9021, message, tags=tags)
        ack = result["ack"]
        if ack[0] == "OK" and ack[2]:
            return {"ok": True, "event_id": result["event"]["id"]}
        return {"ok": False, "error": str(ack)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await sess.close()


async def list_members(
    relay_ws_url: str,
    admin_pk,
    group_id: str,
) -> list:
    """Query kind 9000 events for a group and return pubkeys of added members."""
    sess = NostrSession(relay_ws_url, admin_pk)
    try:
        await sess.connect()
        await sess.authenticate()
        sub_id = uuid.uuid4().hex[:16]
        await sess.subscribe(
            [{"kinds": [9000], "#h": [group_id]}],
            sub_id=sub_id,
        )
        events = await sess.recv_until_eose(sub_id, max_events=200)
        members = set()
        for ev in events:
            for tag in ev.get("tags", []):
                if tag and tag[0] == "p" and len(tag) >= 2:
                    members.add(tag[1])
        return list(members)
    except Exception as e:
        logger.warning("list_members failed for %s: %s", group_id, e)
        return []
    finally:
        await sess.close()
