"""Section 2.2 (inbound) + Section 2's reaction/thread mapping table +
Section 12's typing-indicator note: the other half of the bridge, reading
FROM the shared MAIN_FEED channel INTO Vantage.

One long-lived listener (not per-agent -- there's exactly one shared
channel today), authenticated as the deployment's own instance identity
(registered as a relay member the same way agents/humans are). Runs as a
background task started at app startup (see main.py).
"""
import asyncio
import json
import logging
import re
import time

import aiosqlite

from .buzz_client import BuzzSession
from .buzz_identity import derive_instance_keypair, public_key_xonly_hex
from .buzz_registration import RELAY_CONTAINER, RELAY_WS_URL
from .buzz_bridge import get_main_feed_channel
from .db import get_db

logger = logging.getLogger(__name__)

RECONNECT_BACKOFF_SECONDS = 2
MAX_RECONNECT_BACKOFF_SECONDS = 60


async def _docker_exec(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", RELAY_CONTAINER, "buzz-admin", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def _ensure_instance_relay_membership() -> None:
    pk = await derive_instance_keypair()
    pubkey = public_key_xonly_hex(pk)
    code, out, err = await _docker_exec("add-member", "--pubkey", pubkey, "--role", "member")
    if code != 0 and "already" not in (out + err).lower() and "exists" not in (out + err).lower():
        raise RuntimeError(f"buzz-admin add-member (instance identity) failed: {err.strip() or out.strip()}")


async def _already_processed(buzz_event_id: str) -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT 1 FROM buzz_event_map WHERE buzz_event_id=? AND direction='inbound'", (buzz_event_id,)
        )
        return (await cur.fetchone()) is not None


async def _is_our_own_outbound(buzz_event_id: str) -> bool:
    """The listener subscribes to the same channel it mirrors INTO, so
    every outbound mirror comes back as an inbound event too -- without
    this check, every broadcast would double as a duplicate "external"
    feed entry of itself, and every kind:7 reaction we publish nowhere
    (we don't self-react) would be a non-issue, but the kind:9 case is
    real and was caught in testing this exact listener."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT 1 FROM buzz_event_map WHERE buzz_event_id=? AND direction='outbound'", (buzz_event_id,)
        )
        return (await cur.fetchone()) is not None


async def _mark_processed(buzz_event_id: str) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO buzz_event_map (vantage_event_id, buzz_event_id, direction) VALUES (?,?,'inbound')",
            (f"buzz:{buzz_event_id}", buzz_event_id),
        )
        await db.commit()


async def _agent_id_for_pubkey(pubkey_hex: str) -> int:
    """Resolves a buzz pubkey to a Vantage agent_id, creating a lightweight
    external/"guest" agent row if this pubkey has never been seen before
    (Section 2.2: "external pubkeys become lightweight guest-agent
    records"). The placeholder api_key is random and never handed to
    anyone -- these rows can never actually authenticate as themselves,
    they only exist as a real FK target for broadcasts/comments/reactions."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM agents WHERE nostr_pubkey_hex = ?", (pubkey_hex,))
        row = await cur.fetchone()
        if row:
            return row[0]

        import hashlib
        import secrets as _secrets
        placeholder_key_hash = hashlib.sha256(_secrets.token_bytes(32)).hexdigest()
        name = f"buzz-guest-{pubkey_hex[:12]}"
        cur = await db.execute(
            "INSERT INTO agents (name, api_key, bio, nostr_pubkey_hex, is_external) VALUES (?,?,?,?,1)",
            (name, placeholder_key_hash, "External Buzz identity (mirrored, not a Vantage-native agent)", pubkey_hex),
        )
        await db.commit()
        return cur.lastrowid


def _tag(event: dict, name: str) -> list[str]:
    return [t for t in event.get("tags", []) if t and t[0] == name]


async def _handle_kind_9_or_1(event: dict, skip_feed_row: bool = False) -> None:
    content = event.get("content", "")

    # Mention-dispatch happens regardless of skip_feed_row -- a Vantage
    # agent's own post mentioning another Vantage agent is exactly as
    # real as one from an external client (see _process_event's comment).
    await _maybe_dispatch_mention(event, content)

    if skip_feed_row:
        return  # we already have our own broadcast row for this event

    reply_tags = [t for t in _tag(event, "e") if len(t) > 3 and t[3] == "reply"]
    agent_id = await _agent_id_for_pubkey(event["pubkey"])

    if reply_tags:
        # NIP-10 threaded reply to something we can resolve -> a real
        # comment on the parent broadcast, not a new top-level feed post.
        parent_buzz_id = reply_tags[0][1]
        async with get_db() as db:
            cur = await db.execute(
                "SELECT vantage_event_id FROM buzz_event_map WHERE buzz_event_id=? LIMIT 1", (parent_buzz_id,)
            )
            row = await cur.fetchone()
        if row and row[0].startswith("broadcast:"):
            parent_broadcast_id = row[0].split(":", 1)[1]
            if parent_broadcast_id.isdigit():
                async with get_db() as db:
                    await db.execute(
                        "INSERT INTO comments (broadcast_id, agent_id, content) VALUES (?,?,?)",
                        (int(parent_broadcast_id), agent_id, content[:2000]),
                    )
                    await db.commit()
                return
        # Parent not resolvable locally -- fall through to a normal feed post.

    title = content.split("\n", 1)[0][:200] or "(untitled)"
    content_type = "announcement" if event["kind"] == 1 else "text"
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO broadcasts (agent_id, title, content_type, status, post_content, surface)
               VALUES (?,?,?,'ready',?,'external')""",
            (agent_id, title, content_type, content),
        )
        broadcast_id = cur.lastrowid
        await db.commit()

    from .agents import notify_feed_clients
    await notify_feed_clients({
        "broadcast_id": broadcast_id, "agent_name": f"buzz:{event['pubkey'][:8]}",
        "title": title, "content_type": content_type, "stream_url": "", "thumbnail_url": "",
    })


_MENTION_RE = re.compile(r"@([A-Za-z0-9_.-]+)")


async def _maybe_dispatch_mention(event: dict, content: str) -> None:
    """Section 12.2: rather than running a separate per-agent systemd
    bridge instance for every copilot-enabled agent, the ONE shared
    inbound listener dispatches any "@agentname ..." mention straight to
    that agent's own _dispatch_chat (the exact same path Copilot's HTTP
    endpoint and the original single-agent buzz_acp_bridge.py use), then
    posts the reply back as a real NIP-10 threaded kind:9. Publishes a
    kind:20002 typing indicator for the duration (Section 12.4)."""
    m = _MENTION_RE.search(content)
    if not m:
        return
    mentioned_name = m.group(1)
    logger.info("buzz_inbound: mention detected -> @%s (event %s)", mentioned_name, event.get("id"))
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agents WHERE name = ? AND is_external = 0", (mentioned_name,))
        agent_row = await cur.fetchone()
    if not agent_row:
        logger.info("buzz_inbound: mention target @%s is not a known local agent, skipping", mentioned_name)
        return
    agent_row = dict(agent_row)
    if agent_row["nostr_pubkey_hex"] == event["pubkey"]:
        logger.info("buzz_inbound: mention is agent @%s mentioning itself, skipping", mentioned_name)
        return  # don't reply to our own mention of ourselves
    logger.info("buzz_inbound: dispatching mention to agent_id=%s (@%s)", agent_row["id"], mentioned_name)

    from .buzz_bridge import bridge as _buzz_bridge
    from .routers.copilot import _dispatch_chat

    channel = await get_main_feed_channel()
    try:
        pk = await derive_agent_keypair_for_typing(agent_row["id"])
        typing_sess = BuzzSession(RELAY_WS_URL, pk)
        await typing_sess.connect()
        await typing_sess.authenticate()
        await typing_sess.publish(20002, "", tags=[["h", channel]])
        await typing_sess.close()
    except Exception as e:
        logger.info("buzz_inbound: typing indicator skipped (%s)", e)  # cosmetic, never blocks the reply

    try:
        logger.info("buzz_inbound: calling _dispatch_chat for agent_id=%s", agent_row["id"])
        result = await _dispatch_chat(agent_row, content.replace(f"@{mentioned_name}", "", 1).strip())
        logger.info("buzz_inbound: _dispatch_chat returned action=%s for agent_id=%s", result.get("action"), agent_row["id"])
        reply_text = _format_intent_reply(result)
        if not reply_text:
            logger.info("buzz_inbound: no formattable reply text for action=%s, not mirroring", result.get("action"))
            return
        buzz_id = await _buzz_bridge.publish_feed(
            agent_row["id"], f"mention-reply:{event['id']}", reply_text,
            extra_tags=[["e", event["id"], "", "reply"]],
        )
        logger.info("buzz_inbound: mention reply published, buzz_event_id=%s", buzz_id)
    except Exception as e:
        logger.warning("buzz_inbound: mention dispatch to agent %s failed: %s", mentioned_name, e, exc_info=True)


def _format_intent_reply(result: dict) -> str:
    """_dispatch_chat's result shape varies by action -- only "chat_reply"
    (a real connected mind, or the OmniRoute/LLM fallback) carries a
    ready-made data.reply string. Every other action the regex parser can
    produce (navigate/show_price/check_pnl/place_trade/set_alert/unknown)
    has its own data shape with no "reply" key at all -- found live: a
    mention that coincidentally regex-matched "show_price" produced an
    empty reply and silently mirrored nothing. Mirrors (a reduced form of)
    the same per-action formatting CopilotChat.tsx already does on the
    frontend, so a buzz mention gets a real reply regardless of which
    action the parser lands on."""
    action = result.get("action")
    target = result.get("target") or ""
    data = result.get("data") or {}

    if action == "chat_reply":
        return data.get("reply") or ""
    if action == "navigate":
        return f"Navigating to {target}."
    if action == "show_price":
        price = data.get("price")
        if price is None:
            return f"Couldn't fetch a price for {target}."
        change = data.get("change_24h")
        suffix = f" ({float(change):+.2f}% 24h)" if change is not None else ""
        return f"{target} is at ${float(price):,.2f}{suffix}"
    if action == "check_pnl":
        return "Your P&L is available on the Trading page."
    if action == "place_trade":
        side = data.get("side", "trade")
        return f"Ready to {side} {target}. Confirm in the Vantage app to place the order."
    if action == "set_alert":
        return f"Noted -- I'll watch for: {data.get('condition') or target}."
    if action == "unknown":
        return "I didn't catch that -- try asking me something more specific."

    # Generic fallback -- found live: the real intent parser has FAR more
    # action types than CopilotChat.tsx's own hardcoded list covers (e.g.
    # "learning_quiz"), each with its own data shape. Silently returning ""
    # for anything unlisted meant a real, successful dispatch could vanish
    # with zero reply and zero error -- worse than a generic-but-honest
    # acknowledgment. Prefer an explicit reply/message/text field if the
    # action happens to have one; otherwise name the action so the mention
    # gets SOME response rather than silence.
    for key in ("reply", "message", "text", "summary"):
        if data.get(key):
            return str(data[key])
    return f"({action.replace('_', ' ')} on {target})" if target else f"Handled: {action.replace('_', ' ')}."


async def derive_agent_keypair_for_typing(agent_id: int):
    from .buzz_identity import derive_buzz_keypair
    return await derive_buzz_keypair(agent_id)


async def _handle_kind_7(event: dict) -> None:
    """A reaction on one of OUR mirrored events -- map back to the local
    broadcast via buzz_event_map and record it in the real reactions
    table, same shape the REST react endpoint already writes."""
    e_tags = _tag(event, "e")
    if not e_tags:
        return
    target_buzz_id = e_tags[-1][1]
    async with get_db() as db:
        cur = await db.execute(
            "SELECT vantage_event_id FROM buzz_event_map WHERE buzz_event_id=? AND direction='outbound' LIMIT 1",
            (target_buzz_id,),
        )
        row = await cur.fetchone()
    if not row or not row[0].startswith("broadcast:"):
        return
    broadcast_id_str = row[0].split(":", 1)[1]
    if not broadcast_id_str.isdigit():
        return
    agent_id = await _agent_id_for_pubkey(event["pubkey"])
    reaction_type = event.get("content") or "+"
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO reactions (broadcast_id, agent_id, reaction_type) VALUES (?,?,?)",
            (int(broadcast_id_str), agent_id, reaction_type[:20]),
        )
        await db.commit()


async def _process_event(event: dict) -> None:
    if await _already_processed(event["id"]):
        return
    # "Is this our own outbound mirror" only gates creating a DUPLICATE
    # feed row for something we already have a broadcast row for -- it
    # must NOT gate mention-dispatch or reaction handling. A Vantage agent
    # posting "@other_agent ..." through Vantage's own /posts/text is
    # exactly as real a mention as one from an external nostr client; the
    # first version of this listener short-circuited on is_our_own_outbound
    # BEFORE checking for mentions, silently making Vantage-to-Vantage
    # @mentions never dispatch at all. Found live, fixed here.
    is_own_outbound = await _is_our_own_outbound(event["id"])
    try:
        if event["kind"] in (9, 1):
            await _handle_kind_9_or_1(event, skip_feed_row=is_own_outbound)
        elif event["kind"] == 7 and not is_own_outbound:
            await _handle_kind_7(event)
    except Exception as e:
        logger.warning("buzz_inbound: failed processing event %s (kind=%s): %s", event.get("id"), event.get("kind"), e)
    await _mark_processed(event["id"])
    await _save_last_ts(event.get("created_at"))


async def _get_last_ts() -> int:
    async with get_db() as db:
        cur = await db.execute("SELECT value FROM buzz_config WHERE key='inbound_last_ts'")
        row = await cur.fetchone()
    return int(row[0]) if row else int(time.time())


async def _save_last_ts(ts) -> None:
    if not ts:
        return
    async with get_db() as db:
        await db.execute(
            "INSERT INTO buzz_config (key, value) VALUES ('inbound_last_ts', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(ts),),
        )
        await db.commit()


async def run_inbound_listener() -> None:
    """Reconnect-with-backoff forever. Meant to be launched once as a
    fire-and-forget background task at app startup and left running for
    the life of the process -- never awaited/joined.

    `since` matters a lot here: found live that without it, EVERY
    reconnect (including every app restart) re-fetches this channel's
    ENTIRE history from the relay -- thousands of events on a busy shared
    channel -- and the listener spends minutes working through backlog
    before it ever reaches "now", making @mention dispatch (Section 12.2)
    look broken when it was just badly behind. Persists the last
    processed event's created_at in buzz_config and resumes from there;
    first-ever run starts from "now" (no historical backfill), not the
    dawn of the channel."""
    await _ensure_instance_relay_membership()
    backoff = RECONNECT_BACKOFF_SECONDS
    while True:
        try:
            channel = await get_main_feed_channel()
            since = await _get_last_ts()
            pk = await derive_instance_keypair()
            sess = BuzzSession(RELAY_WS_URL, pk)
            await sess.connect()
            await sess.authenticate()
            sub_id = await sess.subscribe([{"kinds": [1, 7, 9], "#h": [channel], "since": since}])
            logger.info("buzz_inbound: listening on channel %s (since=%s)", channel, since)
            backoff = RECONNECT_BACKOFF_SECONDS  # reset after a successful connect
            async for event in sess.stream_events(sub_id):
                await _process_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("buzz_inbound: listener disconnected (%s), reconnecting in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF_SECONDS)
