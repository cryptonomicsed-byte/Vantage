"""G6 (repo provider and bindings), G7 (sandbox worktrees) and G9 (trading
workspace completion).

The repo host is stood in for by a fake transport on the provider; the
sandbox by the same fake the workspace tests use. What is asserted is what
Vantage owns: bind fails for unknown repos, the branch and PR belong to
the claim holder under the guild naming, the artifact hash is the hash of
the diff, a signed merge webhook closes the task and an unsigned one
changes nothing, and the trading tabs are guild-scoped and paper-only.
"""
import hashlib
import hmac
import json
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import repo_providers
from backend.config import settings
from backend.db import DB_PATH
from backend.repo_providers.github import GitHubProvider
from backend.routers import workspace

pytestmark = pytest.mark.asyncio


async def _agent(db, guild_id=None, role="member"):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"G6_{hashed[:10]}"
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
        slug = f"g6-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", founder["id"], founder["name"], f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, founder["id"], founder["name"]))
        a = await _agent(db, gid)
        b = await _agent(db, gid)
        cur = await db.execute(
            "INSERT INTO guild_channels (guild_id, slug, name, channel_kind) VALUES (?,?,?,'workspace')",
            (gid, "eng", "Engineering"))
        ws_id = cur.lastrowid
        await db.commit()
    return {"slug": slug, "id": gid, "founder": founder, "a": a, "b": b, "ws_id": ws_id}


class FakeHost:
    """A GitHub that knows one repo and records what it was asked."""

    def __init__(self):
        self.calls = []
        self.repos = {"acme/widgets": {"default_branch": "main", "private": False,
                                       "html_url": "https://github.com/acme/widgets",
                                       "clone_url": "https://github.com/acme/widgets.git",
                                       "permissions": {"push": True}}}
        self.opened = {}   # PRs the agent opened itself: number -> (head, base)

    async def __call__(self, method, path, json):
        self.calls.append((method, path, json))
        parts = path.strip("/").split("/")
        if parts[0] == "repos":
            full = f"{parts[1]}/{parts[2]}"
            if full not in self.repos:
                return 404, {"message": "Not Found"}
            if len(parts) == 3:
                return 200, self.repos[full]
            if parts[3] == "git" and parts[4] == "ref":
                return 200, {"object": {"sha": "abc123"}}
            if parts[3] == "git" and parts[4] == "refs":
                return 201, {"ref": json["ref"]}
            if parts[3] == "pulls" and method == "GET" and len(parts) == 5:
                n = int(parts[4])
                if n not in self.opened:
                    return 404, {"message": "Not Found"}
                head, base = self.opened[n]
                return 200, {"number": n, "html_url": f"https://github.com/{full}/pull/{n}", "state": "open",
                             "head": {"ref": head}, "base": {"ref": base}}
        return 404, None


@pytest.fixture
def host(monkeypatch):
    fake = FakeHost()
    monkeypatch.setattr(repo_providers, "get_provider",
                        lambda name, token=None: GitHubProvider(token="t", transport=fake))
    return fake


@pytest.fixture
def sandbox(monkeypatch):
    calls, replies = [], {}

    async def fake(endpoint, payload):
        calls.append((endpoint, payload))
        return replies.get(endpoint, {"exit_code": 0, "stdout": "", "stderr": ""})

    monkeypatch.setattr(workspace, "CODE_SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setattr(workspace, "_sandbox", fake)
    return {"calls": calls, "replies": replies}


async def _bind(client, guild, repo="acme/widgets", who="founder"):
    owner, name = repo.split("/")
    return await client.post(f"/api/guilds/{guild['slug']}/channels/eng/repo",
                             json={"repo_owner": owner, "repo_name": name, "provider": "github"},
                             headers=guild[who]["headers"])


# ── G6 ────────────────────────────────────────────────────────────────────────

async def test_bind_fails_for_an_unknown_repo_and_verifies_a_known_one(client, guild, host):
    # The forum's principal auth needs a principal row for the founder.
    await coord.get_or_create_agent_principal(guild["founder"]["id"])
    await coord.add_membership(guild["id"], await coord.get_or_create_agent_principal(guild["founder"]["id"]), role="founder")
    r = await _bind(client, guild, repo="acme/nope")
    assert r.status_code == 422, r.text
    r = await _bind(client, guild)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] and body["html_url"] == "https://github.com/acme/widgets" and body["default_branch"] == "main"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        ch = await (await db.execute("SELECT * FROM guild_channels WHERE id=?", (guild["ws_id"],))).fetchone()
    assert ch["repo_provider"] == "github" and ch["repo_verified_at"] and ch["repo_html_url"]


async def _bound_task(client, guild, host):
    fp = await coord.get_or_create_agent_principal(guild["founder"]["id"])
    await coord.add_membership(guild["id"], fp, role="founder")
    assert (await _bind(client, guild)).status_code == 200
    base = f"/api/guilds/{guild['slug']}/workspaces/{guild['ws_id']}/tasks"
    tid = (await client.post(base, json={"title": "add the widget"}, headers=guild["founder"]["headers"])).json()["id"]
    return tid


async def test_branch_and_pr_belong_to_the_claim_holder(client, guild, host):
    tid = await _bound_task(client, guild, host)
    slug = guild["slug"]
    ws_base = f"/api/guilds/{slug}/workspaces/{guild['ws_id']}/tasks/{tid}"
    work = f"/api/guilds/{slug}/work/vtask/{tid}"
    # Not claimed yet: nobody may branch.
    assert (await client.post(f"{work}/branch", headers=guild["a"]["headers"])).status_code == 403
    assert (await client.post(f"{ws_base}/claim", headers=guild["a"]["headers"])).status_code == 200
    # The claim holder is a contributor; push_branch needs operator. Give it.
    async with aiosqlite.connect(DB_PATH) as db:
        pa = await coord.get_or_create_agent_principal(guild["a"]["id"])
    from backend import workspace_roles as wsr
    await wsr.set_role(channel_id=guild["ws_id"], principal_id=pa["id"], role="operator")
    r = await client.post(f"{work}/branch", headers=guild["a"]["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == f"guild/{slug}/{tid}" and r.json()["from"] == "main"
    assert r.json()["created_by_vantage"] is False
    # Read-only toward the host: nothing but GETs, ever.
    assert host.calls and all(m == "GET" for m, _, _ in host.calls)
    # The agent opens the PR itself; a PR from the wrong branch is refused.
    host.opened[1] = (f"guild/{slug}/{tid}", "main")
    host.opened[2] = ("someone-else", "main")
    assert (await client.post(f"{work}/pr", json={"number": 1}, headers=guild["b"]["headers"])).status_code == 403
    assert (await client.post(f"{work}/pr", json={"number": 2}, headers=guild["a"]["headers"])).status_code == 422
    assert (await client.post(f"{work}/pr", json={"number": 9}, headers=guild["a"]["headers"])).status_code == 404
    r = await client.post(f"{work}/pr", json={"number": 1}, headers=guild["a"]["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["pr"] == "pr:acme/widgets#1" and r.json()["head"] == f"guild/{slug}/{tid}"
    assert all(m == "GET" for m, _, _ in host.calls)
    async with aiosqlite.connect(DB_PATH) as db:
        st = (await (await db.execute("SELECT status FROM vantage_tasks WHERE id=?", (tid,))).fetchone())[0]
    assert st == "review"
    return tid


async def test_signed_merge_webhook_closes_the_task_and_unsigned_does_not(client, guild, host, monkeypatch):
    tid = await test_branch_and_pr_belong_to_the_claim_holder(client, guild, host)
    monkeypatch.setattr(settings, "REPO_WEBHOOK_SECRET", "hook-secret")
    payload = json.dumps({"action": "closed", "repository": {"full_name": "acme/widgets"},
                          "pull_request": {"number": 1, "merged": True, "merge_commit_sha": "deadbeef",
                                           "head": {"ref": f"guild/{guild['slug']}/{tid}"}}}).encode()
    bad = await client.post("/api/hooks/github", content=payload,
                            headers={"content-type": "application/json", "x-github-event": "pull_request",
                                     "x-hub-signature-256": "sha256=" + "0" * 64})
    assert bad.status_code == 401
    sig = "sha256=" + hmac.new(b"hook-secret", payload, hashlib.sha256).hexdigest()
    ok = await client.post("/api/hooks/github", content=payload,
                           headers={"content-type": "application/json", "x-github-event": "pull_request",
                                    "x-github-delivery": secrets.token_hex(8), "x-hub-signature-256": sig})
    assert ok.status_code == 200 and tid in ok.json()["closed_tasks"], ok.text
    async with aiosqlite.connect(DB_PATH) as db:
        st = (await (await db.execute("SELECT status FROM vantage_tasks WHERE id=?", (tid,))).fetchone())[0]
        art = (await (await db.execute("SELECT status FROM artifacts WHERE task_id=? AND kind='pr'", (tid,))).fetchone())[0]
    assert st == "closed" and art == "accepted"


async def test_gitea_provisioning_is_retired(client, guild, monkeypatch):
    from backend import gitea_accounts
    monkeypatch.setattr(gitea_accounts, "ADMIN_TOKEN", "configured")   # even when configured
    r = await client.post("/api/agents/me/gitea", headers=guild["a"]["headers"])
    assert r.status_code == 410


# ── G7 ────────────────────────────────────────────────────────────────────────

async def test_worktree_and_artifact_hash_match_the_diff(client, guild, host, sandbox):
    tid = await _bound_task(client, guild, host)
    slug = guild["slug"]
    ws_base = f"/api/guilds/{slug}/workspaces/{guild['ws_id']}/tasks/{tid}"
    work = f"/api/guilds/{slug}/work/vtask/{tid}"
    assert (await client.post(f"{ws_base}/claim", headers=guild["a"]["headers"])).status_code == 200
    pa = await coord.get_or_create_agent_principal(guild["a"]["id"])
    from backend import workspace_roles as wsr
    await wsr.set_role(channel_id=guild["ws_id"], principal_id=pa["id"], role="operator")

    sandbox["replies"]["/worktree/add"] = {"dir": f"agent-{guild['a']['id']}-x/widgets", "branch": "agent/x/task/y"}
    r = await client.post(f"{work}/worktree", headers=guild["a"]["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["task_branch"] == f"guild/{slug}/{tid}" and r.json()["repo"] == "acme/widgets"
    assert any(e.startswith("/worktree") for e, _ in sandbox["calls"])

    patch = "diff --git a/w.py b/w.py\n+print('hi')\n"
    outputs = iter([{"exit_code": 0, "stdout": "cafebabe\n", "stderr": ""}, {"exit_code": 0, "stdout": patch, "stderr": ""}])

    async def exec_fake(endpoint, payload):
        sandbox["calls"].append((endpoint, payload))
        return next(outputs) if endpoint == "/exec" else {"exit_code": 0, "stdout": "", "stderr": ""}
    workspace._sandbox = exec_fake  # noqa: the fixture restores the module attribute
    r = await client.post(f"{work}/artifact", json={"title": "widget diff"}, headers=guild["a"]["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["commit"] == "cafebabe" and body["content_hash"] == hashlib.sha256(patch.encode()).hexdigest()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        art = await (await db.execute("SELECT * FROM artifacts WHERE id=?", (body["artifact_id"],))).fetchone()
    assert art["kind"] == "diff" and art["content_uri"] == "commit:cafebabe" and art["content_hash"] == body["content_hash"]
    # The exec ran inside the agent's own directory, never elsewhere.
    execs = [p for e, p in sandbox["calls"] if e == "/exec"]
    assert execs and all(p["cwd"].startswith(f"agent-{guild['a']['id']}-") for p in execs)


# ── G9 ────────────────────────────────────────────────────────────────────────

async def test_research_aliases_cover_every_sub_tab():
    from backend.routers.trading_space import RESEARCH_ALIASES
    assert set(RESEARCH_ALIASES) == {"alpha", "signals", "wallet", "degen", "narratives", "watch"}


async def test_space_orders_and_strategies_are_guild_scoped_and_paper_only(client, guild, app):
    slug, h = guild["slug"], guild["a"]["headers"]
    sid = (await client.post(f"/api/guilds/{slug}/trading-spaces", json={"name": "S"}, headers=h)).json()["id"]
    base = f"/api/guilds/{slug}/trading-spaces/{sid}"
    for path in ("orders", "strategies"):
        r = await client.get(f"{base}/{path}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["paper_only"] is True and "items" in r.json()
    async with aiosqlite.connect(DB_PATH) as db:
        outsider = await _agent(db)
        await db.commit()
    assert (await client.get(f"{base}/orders", headers=outsider["headers"])).status_code == 403
    # No route under the guild's trading spaces touches the live path.
    from fastapi.routing import APIRoute
    live = [r.path for r in app.router.routes if isinstance(r, APIRoute)
            and r.path.startswith("/api/guilds/{slug}/trading-spaces") and "live" in r.path]
    assert live == []
