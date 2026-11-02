"""content_commands.py -- slash command handlers for content collab channels.

Content channels route video / audio / podcast creation through whichever
MCP provider is configured on the channel (`channel["mcp_provider"]`).

Supported providers
-------------------
  higgsfield   — video generation via Composio toolkit
  heygen       — video / avatar generation via Composio toolkit
  vantage_anvil — free default; uses the local Vantage Anvil ingest endpoint

Production project lifecycle
-----------------------------
Each content channel owns at most one active production_projects row, tracked
in channel_productions.  Agents add scenes / tracks via /scene; the director
calls /publish to ship the assembled production to Cinema or Audio.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _ok(reply: str, **data) -> dict:
    return {"reply": reply, "data": data}

def _err(msg: str) -> dict:
    return {"reply": f":x: {msg}", "data": {"error": msg}}


# ── MCP routing ───────────────────────────────────────────────────────────────

async def _route_to_provider(
    provider: str,
    task_type: str,
    prompt: str,
    agent: dict,
    db,
) -> dict:
    """Route a generation request to the right provider.

    Returns {"ok": bool, "result": str, "url": str, "job_id": str}.
    """
    provider = (provider or "vantage_anvil").lower().strip()

    if provider in ("higgsfield", "heygen"):
        # Verify the agent has an active Composio connection for this toolkit.
        conn = await (await db.execute(
            "SELECT id FROM composio_connections WHERE agent_id=? AND toolkit_slug=?",
            (agent["id"], provider),
        )).fetchone()
        if not conn:
            return {
                "ok": False,
                "result": (
                    f"No {provider} connection found. "
                    f"Use /setmcp vantage_anvil for the free tier, "
                    f"or connect {provider} via Composio at /api/composio/toolkits/{provider}/connect."
                ),
                "url": "",
                "job_id": "",
            }
        # Execute via the Composio execute endpoint (in-process SDK).
        action_map = {
            ("higgsfield", "video"): "HIGGSFIELD_AI_GENERATE_VIDEO",
            ("higgsfield", "audio"): "HIGGSFIELD_AI_GENERATE_AUDIO",
            ("heygen",     "video"): "HEYGEN_GENERATE_VIDEO",
            ("heygen",     "audio"): "HEYGEN_GENERATE_AUDIO",
        }
        tool_slug = action_map.get((provider, task_type), f"{provider.upper()}_GENERATE_{task_type.upper()}")
        try:
            async with httpx.AsyncClient(timeout=30) as cl:
                r = await cl.post(
                    "http://localhost:8001/api/composio/execute",
                    json={"tool_slug": tool_slug, "arguments": {"prompt": prompt}},
                    headers={
                        "Authorization": f"Bearer {agent.get('api_key', '')}",
                        "X-Agent-Id": str(agent.get("id", "")),
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    result_data = data.get("result") or {}
                    job_id = (
                        result_data.get("job_id")
                        or result_data.get("id")
                        or result_data.get("taskId")
                        or "queued"
                    )
                    url = result_data.get("url") or result_data.get("preview_url") or ""
                    return {"ok": True, "result": str(result_data), "url": url, "job_id": str(job_id)}
                else:
                    return {
                        "ok": False,
                        "result": f"Composio returned {r.status_code}: {r.text[:200]}",
                        "url": "", "job_id": "",
                    }
        except Exception as e:
            logger.warning("_route_to_provider composio error: %s", e)
            return {"ok": False, "result": str(e), "url": "", "job_id": ""}

    else:  # vantage_anvil (free default)
        try:
            async with httpx.AsyncClient(timeout=30) as cl:
                r = await cl.post(
                    "http://localhost:8001/api/vantage/anvil/ingest-video",
                    json={"prompt": prompt, "task_type": task_type, "agent_id": agent.get("id")},
                    headers={
                        "Authorization": f"Bearer {agent.get('api_key', '')}",
                        "X-Agent-Id": str(agent.get("id", "")),
                    },
                )
                if r.status_code in (200, 201, 202):
                    data = r.json()
                    job_id = (
                        data.get("job_id") or data.get("id")
                        or data.get("ingest_id") or "queued"
                    )
                    url = data.get("url") or data.get("preview_url") or ""
                    return {"ok": True, "result": str(data), "url": url, "job_id": str(job_id)}
                else:
                    return {
                        "ok": False,
                        "result": f"Anvil returned {r.status_code}: {r.text[:200]}",
                        "url": "", "job_id": "",
                    }
        except Exception as e:
            logger.warning("_route_to_provider anvil error: %s", e)
            return {"ok": False, "result": str(e), "url": "", "job_id": ""}


# ── production project helpers ────────────────────────────────────────────────

async def _get_or_create_production(db, channel: dict, agent: dict) -> Optional[dict]:
    """Return (or create) the channel_productions row + linked production_project."""
    channel_id = channel["id"]

    cp_row = await (await db.execute(
        "SELECT * FROM channel_productions WHERE channel_id=? AND status='active' ORDER BY id DESC LIMIT 1",
        (channel_id,),
    )).fetchone()

    if cp_row:
        cp = dict(cp_row)
        if cp.get("production_id"):
            proj_row = await (await db.execute(
                "SELECT * FROM production_projects WHERE id=? AND status != 'archived'",
                (cp["production_id"],),
            )).fetchone()
            if proj_row:
                return {"cp": cp, "project": dict(proj_row)}

    # Create a fresh production project and link it.
    channel_name = channel.get("name") or channel.get("slug") or str(channel_id)

    cur = await db.execute(
        """INSERT INTO production_projects
             (owner_id, owner_name, title, description, medium, target_surface,
              cover_url, synopsis, category, cinema_kind, status)
           VALUES (?, ?, ?, ?, 'video', 'cinema', '', '', '', 'movie', 'open')""",
        (
            agent["id"],
            agent.get("name", str(agent["id"])),
            f"{channel_name} Production",
            f"Auto-created production for channel #{channel_id}",
        ),
    )
    pid = cur.lastrowid

    # Add the creating agent as director collaborator.
    await db.execute(
        "INSERT OR IGNORE INTO production_collaborators (project_id, agent_id, agent_name, role) VALUES (?,?,?,'director')",
        (pid, agent["id"], agent.get("name", str(agent["id"]))),
    )

    # Create channel_productions link.
    provider = channel.get("mcp_provider") or "vantage_anvil"
    await db.execute(
        "INSERT INTO channel_productions (channel_id, production_id, mcp_provider, status) VALUES (?,?,?,'active')",
        (channel_id, pid, provider),
    )
    await db.commit()

    # Re-fetch.
    cp_row = await (await db.execute(
        "SELECT * FROM channel_productions WHERE channel_id=? AND production_id=?",
        (channel_id, pid),
    )).fetchone()
    proj_row = await (await db.execute(
        "SELECT * FROM production_projects WHERE id=?", (pid,),
    )).fetchone()
    return {"cp": dict(cp_row), "project": dict(proj_row)}


async def _get_production_row(db, pid: int) -> Optional[dict]:
    row = await (await db.execute(
        "SELECT * FROM production_projects WHERE id=?", (pid,),
    )).fetchone()
    return dict(row) if row else None


async def _ensure_collaborator(db, pid: int, agent_id: int, agent_name: str) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO production_collaborators (project_id, agent_id, agent_name, role) VALUES (?,?,?,'contributor')",
        (pid, agent_id, agent_name),
    )


# ── command handlers ──────────────────────────────────────────────────────────

async def _cmd_video(args: str, channel: dict, agent: dict, db) -> dict:
    """Generate a video via the channel's MCP provider."""
    if not args:
        return _err("Usage: /video <prompt>")

    provider = channel.get("mcp_provider") or "vantage_anvil"
    prod = await _get_or_create_production(db, channel, agent)
    if not prod:
        return _err("Could not create or load a production project for this channel.")

    pid = prod["project"]["id"]
    title = prod["project"]["title"]

    result = await _route_to_provider(provider, "video", args, agent, db)
    if not result["ok"]:
        return _err(f"Provider error ({provider}): {result['result']}")

    job_id = result["job_id"]
    url = result["url"]

    # Record as a scene contribution so collaborators can see it.
    await _ensure_collaborator(db, pid, agent["id"], agent.get("name", str(agent["id"])))
    body = url or f"job:{job_id} -- {args[:120]}"
    await db.execute(
        """INSERT INTO production_contributions
             (project_id, agent_id, agent_name, kind, title, body, duration_sec, order_index)
           VALUES (?,?,?,'scene',?,?,0,0)""",
        (pid, agent["id"], agent.get("name", str(agent["id"])), f"Video: {args[:80]}", body),
    )
    await db.execute(
        "UPDATE production_projects SET status='in_production', updated_at=datetime('now') WHERE id=? AND status='open'",
        (pid,),
    )
    await db.commit()

    reply_parts = [f"Video generation started via **{provider}**. Job: `{job_id}`."]
    if url:
        reply_parts.append(f"Preview: {url}")
    reply_parts.append(f"Linked to production: _{title}_ (id={pid}). Scene will appear when ready.")
    try:
        from backend.utils import _append_receipt
        await _append_receipt(
            str(agent["id"]), "content.create",
            {"verb": "video", "provider": provider, "prompt": args[:100], "job_id": job_id},
        )
    except Exception:
        pass  # fail-open
    return _ok("\n".join(reply_parts), provider=provider, job_id=job_id, url=url, production_id=pid)


async def _cmd_audio(args: str, channel: dict, agent: dict, db) -> dict:
    """Generate audio via the channel's MCP provider."""
    if not args:
        return _err("Usage: /audio <prompt>")

    provider = channel.get("mcp_provider") or "vantage_anvil"
    prod = await _get_or_create_production(db, channel, agent)
    if not prod:
        return _err("Could not load or create a production project.")

    pid = prod["project"]["id"]
    title = prod["project"]["title"]

    result = await _route_to_provider(provider, "audio", args, agent, db)
    if not result["ok"]:
        return _err(f"Provider error ({provider}): {result['result']}")

    job_id = result["job_id"]
    url = result["url"]

    await _ensure_collaborator(db, pid, agent["id"], agent.get("name", str(agent["id"])))
    body = url or f"job:{job_id} -- {args[:120]}"
    await db.execute(
        """INSERT INTO production_contributions
             (project_id, agent_id, agent_name, kind, title, body, duration_sec, order_index)
           VALUES (?,?,?,'track',?,?,0,0)""",
        (pid, agent["id"], agent.get("name", str(agent["id"])), f"Audio: {args[:80]}", body),
    )
    await db.execute(
        "UPDATE production_projects SET updated_at=datetime('now') WHERE id=?", (pid,),
    )
    await db.commit()

    reply_parts = [f"Audio generation started via **{provider}**. Job: `{job_id}`."]
    if url:
        reply_parts.append(f"Preview: {url}")
    reply_parts.append(f"Track added to production: _{title}_ (id={pid}).")
    return _ok("\n".join(reply_parts), provider=provider, job_id=job_id, url=url, production_id=pid)


async def _cmd_podcast(args: str, channel: dict, agent: dict, db) -> dict:
    """Create a podcast episode via POST /api/podcast/create."""
    if not args:
        return _err("Usage: /podcast <topic>")

    try:
        async with httpx.AsyncClient(timeout=30) as cl:
            r = await cl.post(
                "http://localhost:8001/api/podcast/create",
                json={"topic": args, "channel_id": channel.get("id")},
                headers={
                    "Authorization": f"Bearer {agent.get('api_key', '')}",
                    "X-Agent-Id": str(agent.get("id", "")),
                },
            )
            if r.status_code in (200, 201, 202):
                data = r.json()
                job_id = data.get("job_id") or data.get("id") or "queued"
                return _ok(
                    f"Podcast episode started for topic: _{args[:80]}_. Job: `{job_id}`.",
                    job_id=str(job_id),
                )
            else:
                return _err(f"Podcast API returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning("/podcast error: %s", e)
        return _err(f"Podcast creation failed: {e}")


async def _cmd_scene(args: str, channel: dict, agent: dict, db) -> dict:
    """Add a scene/asset URL to the current channel production."""
    if not args:
        return _err("Usage: /scene <url>")

    prod = await _get_or_create_production(db, channel, agent)
    if not prod:
        return _err("Could not load or create a production project.")

    pid = prod["project"]["id"]
    title = prod["project"]["title"]

    await _ensure_collaborator(db, pid, agent["id"], agent.get("name", str(agent["id"])))
    await db.execute(
        """INSERT INTO production_contributions
             (project_id, agent_id, agent_name, kind, title, body, duration_sec, order_index)
           VALUES (?,?,?,'scene','Scene',?,0,0)""",
        (pid, agent["id"], agent.get("name", str(agent["id"])), args.strip()),
    )
    await db.execute(
        "UPDATE production_projects SET status='in_production', updated_at=datetime('now') WHERE id=? AND status='open'",
        (pid,),
    )
    await db.commit()

    count_row = await (await db.execute(
        "SELECT COUNT(*) AS cnt FROM production_contributions WHERE project_id=? AND kind='scene'",
        (pid,),
    )).fetchone()
    n = count_row["cnt"] if count_row else "?"
    return _ok(
        f"Scene added to production _{title}_. Total scenes: {n}.",
        production_id=pid, scene_url=args.strip(), total_scenes=n,
    )


async def _cmd_publish(args: str, channel: dict, agent: dict, db) -> dict:
    """Publish the channel's active production."""
    channel_id = channel["id"]

    cp_row = await (await db.execute(
        "SELECT * FROM channel_productions WHERE channel_id=? AND status='active' ORDER BY id DESC LIMIT 1",
        (channel_id,),
    )).fetchone()
    if not cp_row or not cp_row["production_id"]:
        return _err("No active production in this channel. Start one with /video or /scene.")

    pid = cp_row["production_id"]
    proj = await _get_production_row(db, pid)
    if not proj:
        return _err(f"Production project {pid} not found.")
    if proj["status"] == "published":
        return _err(f"Production _{proj['title']}_ is already published.")

    try:
        async with httpx.AsyncClient(timeout=30) as cl:
            r = await cl.post(
                f"http://localhost:8001/api/productions/{pid}/publish",
                json={"video_url": "", "duration_sec": 0},
                headers={
                    "Authorization": f"Bearer {agent.get('api_key', '')}",
                    "X-Agent-Id": str(agent.get("id", "")),
                },
            )
            if r.status_code in (200, 201, 202):
                data = r.json()
                surface = proj.get("target_surface") or "cinema"
                pub_url = (
                    data.get("url")
                    or data.get("cinema_url")
                    or data.get("audio_url")
                    or f"/api/productions/{pid}"
                )
                await db.execute(
                    "UPDATE channel_productions SET status='published' WHERE channel_id=? AND production_id=?",
                    (channel_id, pid),
                )
                await db.commit()
                try:
                    from backend.utils import _append_receipt
                    await _append_receipt(
                        str(agent["id"]), "content.create",
                        {"verb": "publish", "production_id": pid, "surface": surface, "url": pub_url},
                    )
                except Exception:
                    pass  # fail-open
                return _ok(
                    f"Production _{proj['title']}_ published to **{surface}**. URL: {pub_url}",
                    production_id=pid, surface=surface, url=pub_url,
                )
            else:
                return _err(f"Publish API returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning("/publish error: %s", e)
        return _err(f"Publish failed: {e}")


async def _cmd_collab(args: str, channel: dict, agent: dict, db) -> dict:
    """Add another agent as a collaborator on the channel's active production."""
    agent_name = args.strip()
    if not agent_name:
        return _err("Usage: /collab <agent_name>")

    channel_id = channel["id"]
    cp_row = await (await db.execute(
        "SELECT * FROM channel_productions WHERE channel_id=? AND status='active' ORDER BY id DESC LIMIT 1",
        (channel_id,),
    )).fetchone()
    if not cp_row or not cp_row["production_id"]:
        return _err("No active production. Start one with /video first.")

    pid = cp_row["production_id"]

    target_row = await (await db.execute(
        "SELECT id, name FROM agents WHERE name=?", (agent_name,),
    )).fetchone()
    if not target_row:
        return _err(f"Agent '{agent_name}' not found.")

    target_id = target_row["id"]
    target_name = target_row["name"]

    await db.execute(
        "INSERT OR IGNORE INTO production_collaborators (project_id, agent_id, agent_name, role) VALUES (?,?,?,'contributor')",
        (pid, target_id, target_name),
    )
    await db.commit()

    return _ok(
        f"**{target_name}** added as collaborator on production #{pid}.",
        production_id=pid, collaborator=target_name,
    )


async def _cmd_setmcp(args: str, channel: dict, agent: dict, db) -> dict:
    """Update the channel's mcp_provider."""
    provider = args.strip().lower()
    supported = {"higgsfield", "heygen", "vantage_anvil"}
    if not provider:
        return _err(f"Usage: /setmcp <provider>  Supported: {', '.join(sorted(supported))}")
    if provider not in supported:
        return _err(f"Unsupported provider '{provider}'. Supported: {', '.join(sorted(supported))}")

    channel_id = channel["id"]
    guild_slug = channel.get("guild_slug") or channel.get("guild_id") or ""
    channel_slug = channel.get("slug") or channel.get("name") or str(channel_id)

    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.patch(
                f"http://localhost:8001/api/guilds/{guild_slug}/channels/{channel_slug}/mcp",
                json={"provider": provider},
                headers={
                    "Authorization": f"Bearer {agent.get('api_key', '')}",
                    "X-Agent-Id": str(agent.get("id", "")),
                },
            )
            if r.status_code in (200, 204):
                await db.execute(
                    "UPDATE channel_productions SET mcp_provider=? WHERE channel_id=? AND status='active'",
                    (provider, channel_id),
                )
                await db.commit()
                return _ok(
                    f"Content pipeline set to **{provider}**. New /video commands will use it.",
                    provider=provider,
                )
    except Exception:
        pass

    # Fallback: update DB directly (PATCH endpoint may not exist yet).
    try:
        await db.execute(
            "UPDATE channel_productions SET mcp_provider=? WHERE channel_id=? AND status='active'",
            (provider, channel_id),
        )
        await db.commit()
        return _ok(
            f"Content pipeline set to **{provider}**. New /video commands will use it.",
            provider=provider,
        )
    except Exception as db_err:
        return _err(f"/setmcp DB update failed: {db_err}")


# ── main dispatcher ───────────────────────────────────────────────────────────

_COMMAND_MAP = {
    "/video":   _cmd_video,
    "/audio":   _cmd_audio,
    "/podcast": _cmd_podcast,
    "/scene":   _cmd_scene,
    "/publish": _cmd_publish,
    "/collab":  _cmd_collab,
    "/setmcp":  _cmd_setmcp,
}

_HELP = (
    "**Content channel commands:**\n"
    "- `/video <prompt>` -- generate video via the channel's MCP provider\n"
    "- `/audio <prompt>` -- generate audio/music via the channel's MCP provider\n"
    "- `/podcast <topic>` -- create a podcast episode\n"
    "- `/scene <url>` -- add a scene/asset to the current production\n"
    "- `/publish` -- publish the channel's production to Cinema/Audio\n"
    "- `/collab <agent_name>` -- add an agent as a collaborator\n"
    "- `/setmcp <provider>` -- set the content pipeline (higgsfield | heygen | vantage_anvil)\n"
)


async def handle(cmd: str, args: str, channel: dict, agent: dict, db) -> dict:
    """Main entry point called by channel_slash.dispatch for content channels."""
    cmd_lower = cmd.lower() if cmd.startswith("/") else f"/{cmd.lower()}"

    if cmd_lower == "/help":
        return _ok(_HELP)

    handler = _COMMAND_MAP.get(cmd_lower)
    if handler is None:
        return _ok(f"Unknown command `{cmd}` in content channel.\n\n{_HELP}")

    try:
        return await handler(args, channel, agent, db)
    except Exception as exc:
        logger.exception("content_commands.handle unhandled error for %s", cmd)
        return _err(f"Internal error running {cmd}: {exc}")
