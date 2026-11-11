"""G3, "Unified work item and state machine" (guild handoff, 2026-11-10).

Acceptance: the ledger shows claim, artifact and accept links for every
kind (vtask, gtask and ltask resolve and transition); every list route
returns the {items, total} envelope with a canonical status per row.
"""
import hashlib
import secrets
import time
import uuid

import aiosqlite
import pytest
import pytest_asyncio

from backend import work_refs
from backend.db import DB_PATH
from backend.work_states import canonical_status, envelope

pytestmark = pytest.mark.asyncio


def test_canonical_status_maps_the_four_vocabularies():
    assert canonical_status("vtask", "brief_submitted") == "proposed"
    assert canonical_status("vtask", "in_production") == "claimed"
    assert canonical_status("vtask", "in_review") == "review"
    assert canonical_status("vtask", "published") == "published"
    assert canonical_status("ltask", "pending") == "proposed"
    assert canonical_status("ltask", "completed") == "closed"
    assert canonical_status("jobtask", "submitted") == "review"
    assert canonical_status("jobtask", "approved") == "accepted"
    assert canonical_status("gtask", "weird") == "proposed"


def test_envelope_keeps_the_legacy_key():
    e = envelope("vtask", [{"id": "1", "status": "in_review"}], legacy_key="tasks")
    assert e["total"] == 1 and e["items"] is e["tasks"]
    assert e["items"][0]["status_canonical"] == "review"


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G3_{hashed[:10]}"
    cur = await db.execute("INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)", (name, hashed, "t"))
    aid = cur.lastrowid
    if guild_id is not None:
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,?)",
                         (guild_id, aid, name, role))
    return {"id": aid, "name": name, "headers": {"X-Agent-Key": raw}}


@pytest_asyncio.fixture
async def guild(client):
    async with aiosqlite.connect(DB_PATH) as db:
        founder = await _agent(db)
        slug = f"g3-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        a = await _agent(db, gid)
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "a": a}


async def test_vtask_resolves_and_a_chat_claim_then_artifact_drives_the_task(client, guild):
    from backend import coordination as coord
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    tid = (await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/tasks", json={"title": "x.calls"}, headers=h)).json()["id"]

    ref = await work_refs.resolve(f"vtask:{tid}")
    assert ref and ref.verified and ref.title == "x.calls" and ref.status == "proposed"

    principal = await coord.get_or_create_agent_principal(guild["a"]["id"])
    claim = await work_refs.record_link(event_id=f"ev-{uuid.uuid4()}", channel_id=None, principal=principal,
                                        link_type=work_refs.LINK_CLAIM, raw_work_ref=f"vtask:{tid}")
    assert claim and claim["transitioned"], claim
    art = await work_refs.record_link(event_id=f"ev-{uuid.uuid4()}", channel_id=None, principal=principal,
                                      link_type=work_refs.LINK_ARTIFACT, raw_work_ref=f"vtask:{tid}")
    assert art and art["transitioned"], art
    async with aiosqlite.connect(DB_PATH) as db:
        st, who = await (await db.execute("SELECT status, claimed_by_agent_id FROM vantage_tasks WHERE id=?", (tid,))).fetchone()
    assert st == "review" and who == guild["a"]["id"]
    links = await work_refs.links_for_ref("vtask", tid)
    assert [l["link_type"] for l in links] == ["claim", "artifact"]


async def test_gtask_and_ltask_resolve(client, guild):
    gid = str(uuid.uuid4())
    lid = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO guild_tasks (id, guild_id, guild_slug, title, created_by_id, created_by_name) VALUES (?,?,?,?,?,?)",
            (gid, guild["id"], guild["slug"], "guild job", guild["founder"]["id"], guild["founder"]["name"]))
        await db.execute(
            "INSERT INTO lounge_tasks (id, lounge_id, guild_slug, title, created_by_agent_id) VALUES (?,?,?,?,?)",
            (lid, "lounge-x", guild["slug"], "lounge job", guild["founder"]["id"]))
        await db.commit()
    g = await work_refs.resolve(f"gtask:{gid}")
    l = await work_refs.resolve(f"ltask:{lid}")
    assert g and g.verified and g.title == "guild job" and g.status == "proposed"
    assert l and l.verified and l.title == "lounge job" and l.status == "pending"
    assert await work_refs.resolve("vtask:does-not-exist") is None
    assert await work_refs.resolve("tro:not-an-int") is None  # integer kinds still refuse text


async def test_every_task_list_route_returns_the_envelope(client, guild):
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/tasks", json={"title": "t"}, headers=h)
    cid = (await client.post(f"/api/guilds/{slug}/content-spaces", json={"name": "C"}, headers=h)).json()["id"]
    await client.post(f"/api/guilds/{slug}/content-spaces/{cid}/tasks", json={"title": "c"}, headers=h)
    await client.post(f"/api/guilds/{slug}/tasks", json={"title": "g"}, headers=guild["founder"]["headers"])
    for path in (f"/api/guilds/{slug}/trading-spaces/{sid}/tasks",
                 f"/api/guilds/{slug}/content-spaces/{cid}/tasks",
                 f"/api/guilds/{slug}/tasks"):
        body = (await client.get(path, headers=h)).json()
        assert "items" in body and "total" in body and "tasks" in body, (path, list(body))
        assert body["total"] >= 1 and all("status_canonical" in t for t in body["items"]), path
