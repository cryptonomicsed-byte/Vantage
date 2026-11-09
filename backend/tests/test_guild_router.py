"""P4-08 / P4-10 / P4-13: guild router -- a guild task round-trips:
owner proposes -> agent claims -> agent files intel_record/1 -> router checks,
stores, routes (social_signals.posted_at) -> owner accepts."""
import hashlib
import json
import secrets
import time

import aiosqlite
import pytest
import pytest_asyncio

from backend import guild_router as gr
from backend.db import DB_PATH

pytestmark = pytest.mark.asyncio
MINT = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"  # secret-scan:allow -- public example mint shape


async def _agent(db, guild_id=None, name=None):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = name or f"GR_{hashed[:10]}"
    cur = await db.execute("INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)", (name, hashed, "t"))
    aid = cur.lastrowid
    if guild_id is not None:
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'member')",
                         (guild_id, aid, name))
    return {"id": aid, "name": name, "headers": {"X-Agent-Key": raw}}


@pytest_asyncio.fixture
async def space(client, monkeypatch):
    monkeypatch.setenv("GUILD_ROUTER_ENABLED", "1")
    async with aiosqlite.connect(DB_PATH) as db:
        owner = await _agent(db)
        slug = f"gr-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", owner["id"], "owner", f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, owner["id"], "owner"))
        grok = await _agent(db, gid)
        other = await _agent(db, gid)
        await db.commit()
    monkeypatch.setenv("GUILD_LANE_X_AGENTS", f"{grok['id']}")
    r = await client.post(f"/api/guilds/{slug}/trading-spaces", data={"name": "S"}, headers=owner["headers"])
    sid = r.json()["id"]

    async def new_task(title="x.mentions"):
        r = await client.post(f"/api/guilds/{slug}/trading-spaces/{sid}/tasks", data={"title": title},
                              headers=owner["headers"])
        return f"/api/guilds/{slug}/trading-spaces/{sid}/tasks/{r.json()['id']}", r.json()["id"]
    return {"slug": slug, "sid": sid, "owner": owner, "grok": grok, "other": other, "new_task": new_task}


def _x(status_id="1855000000000000001", user="caller", minutes_ago=5, ca=MINT, **kw):
    it = {"post_url": f"https://x.com/{user}/status/{status_id}", "username": user,
          "posted_at": int(time.time()) - minutes_ago * 60, "text": f"aping ${'PEPE'} {ca}",
          "contract_address": ca, "ticker": "$pepe"}
    it.update(kw)
    return it


def _rec(items, lane="x.mentions"):
    return {"schema": "intel_record/1", "lane": lane, "items": items}


async def test_round_trip_and_routing(client, space):
    base, tid = await space["new_task"]()
    h = space["grok"]["headers"]
    assert (await client.post(base + "/claim", headers=h)).status_code == 200
    sid = f"18550{secrets.randbelow(10**12):012d}"
    r = await client.post(base + "/intel", json=_rec([_x(status_id=sid)]), headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "accepted" and out["routed"] == 1 and out["artifact_id"]
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT platform, username, ticker, contract_address, posted_at, created_at FROM social_signals "
            "WHERE post_url=?", (f"https://x.com/caller/status/{sid}",))).fetchone()
        st = (await (await db.execute("SELECT status FROM vantage_tasks WHERE id=?", (tid,))).fetchone())[0]
    assert row[:4] == ("x", "caller", "PEPE", MINT) and row[4] and row[4] <= row[5]  # posted before stored
    assert st == "review"
    r = await client.post(base + "/accept", data={"note": "ok"}, headers=space["owner"]["headers"])
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    r = await client.get(f"/api/guilds/{space['slug']}/trading-spaces/{space['sid']}/intel", headers=h)
    assert r.json()["records"][0]["status"] == "accepted"


async def test_router_checks(client, space):
    base, _ = await space["new_task"]()
    h = space["grok"]["headers"]
    await client.post(base + "/claim", headers=h)
    bad = [_x(status_id="1", user="a"),                                  # not a status URL
           _x(user="someone", post_url="https://x.com/other/status/185500000001"),  # handle mismatch
           _x(minutes_ago=-60), _x(minutes_ago=8 * 1440),                 # future / too old
           _x(ca="$PEPE"), _x(posted_at=None)]                            # ticker isn't a contract; no time
    r = await client.post(base + "/intel", json=_rec(bad), headers=h)
    assert r.status_code == 422
    reasons = " | ".join(r.json()["detail"]["reasons"])
    for frag in ("status URL", "does not match", "future", "older than 7 days", "exact base58", "posted_at missing"):
        assert frag in reasons, frag
    r = await client.post(base + "/intel", json={"schema": "intel_record/2", "lane": "x.a", "items": []}, headers=h)
    assert r.status_code == 422


async def test_lane_allowlist_claim_and_dedupe(client, space):
    base, _ = await space["new_task"]()
    # not on the lane
    await client.post(base + "/claim", headers=space["other"]["headers"])
    r = await client.post(base + "/intel", json=_rec([_x()]), headers=space["other"]["headers"])
    assert r.status_code == 422 and "allowlist" in json.dumps(r.json())
    # Muse/Q lanes ship closed (open decision 3)
    assert {l["lane"]: l["open"] for l in gr.lanes_view()} == {"x.*": True, "news.*": False, "research.*": False}
    # on the lane but no claim -> the existing submit path refuses
    base2, _ = await space["new_task"]()
    sid = f"18551{secrets.randbelow(10**12):012d}"
    r = await client.post(base2 + "/intel", json=_rec([_x(status_id=sid)]), headers=space["grok"]["headers"])
    assert r.status_code == 403
    # duplicate post is not routed twice
    await client.post(base2 + "/claim", headers=space["grok"]["headers"])
    r = await client.post(base2 + "/intel", json=_rec([_x(status_id=sid)]), headers=space["grok"]["headers"])
    assert r.status_code == 422 and "already routed" in json.dumps(r.json())


async def test_off_returns_503(client, space, monkeypatch):
    monkeypatch.setenv("GUILD_ROUTER_ENABLED", "0")
    base, _ = await space["new_task"]()
    r = await client.post(base + "/intel", json=_rec([_x()]), headers=space["grok"]["headers"])
    assert r.status_code == 503
    r = await client.get("/api/guild-router/status")
    assert r.status_code == 200 and r.json()["schedules"] == [] and r.json()["enabled"] is False


async def test_news_and_research_validation():
    now = int(time.time())
    ok, reasons, _ = gr.check_record(_rec([{"url": "https://n.example/a", "title": "t", "published_at": now - 60}],
                                          "news.headlines"), now)
    assert len(ok) == 1 and not reasons
    ok, reasons, _ = gr.check_record(_rec([{"title": "t"}], "research.notes"), now)
    assert not ok and "body required" in reasons[0]
    k1 = gr.check_item("research.n", {"title": "a", "body": "b"}, now)[2]
    assert k1 == gr.check_item("research.n", {"title": "a", "body": "b"}, now)[2]  # stable dedupe key


async def test_schedules_create_tasks_as_space_owner(client, space, tmp_path):
    p = tmp_path / "sched.json"
    p.write_text(json.dumps([
        {"guild": space["slug"], "space_id": space["sid"], "lane": "x.mentions", "title": "hourly X sweep",
         "every_minutes": 60},
        {"guild": space["slug"], "space_id": "nope", "lane": "x.mentions", "title": "t", "every_minutes": 60},
        {"guild": "g", "space_id": "s", "lane": "tv.clips", "title": "t", "every_minutes": 60},
        {"guild": "g", "space_id": "s", "lane": "x.a", "title": "t", "every_minutes": 1}]))
    scheds, probs = gr.load_schedules(str(p))
    assert len(scheds) == 2 and len(probs) == 2
    now = int(time.time())
    out = await gr.run_schedules(now, str(p))
    assert out["created"] == 1 and out["skipped"] == 1
    assert (await gr.run_schedules(now + 120, str(p)))["created"] == 0          # not due yet
    assert (await gr.run_schedules(now + 3601, str(p)))["created"] == 1         # due again
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT created_by_agent_id, status, kind_tag FROM vantage_tasks WHERE workspace_id=? AND title=?",
            (space["sid"], "hourly X sweep"))).fetchall()
    assert len(rows) == 2 and all(r == (space["owner"]["id"], "proposed", "lane:x.mentions") for r in rows)
    assert gr.load_schedules("") == ([], [])
