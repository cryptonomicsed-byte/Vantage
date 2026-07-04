"""Agent/human API-parity fixes: vault export/import/ttl/sessions-search,
note-links, debate decline, per-notification read, code.py auth gating,
and the federation galaxy route actually being mounted."""
import io
import json
import zipfile

import aiosqlite
import pytest

from backend.db import DB_PATH


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest.mark.asyncio
async def test_vault_export_universal_json(client, fresh_agent):
    agent = await fresh_agent()
    await client.post(f"/api/agents/{agent['name']}/vault/sync", headers=_h(agent))
    r = await client.get(f"/api/agents/{agent['name']}/vault/export?format=universal", headers=_h(agent))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["agent_name"] == agent["name"]
    assert data["okf_version"]
    assert isinstance(data["nodes"], list)
    # index.md/log.md/README.md/workspace docs must not leak into the export
    paths = [n["path"] for n in data["nodes"]]
    assert not any(p.endswith("index.md") for p in paths)
    assert not any(p.startswith("workspace/") for p in paths)


@pytest.mark.asyncio
async def test_vault_import_json_round_trip(client, fresh_agent):
    source = await fresh_agent()
    dest = await fresh_agent()
    await client.post(f"/api/agents/{source['name']}/vault/sync", headers=_h(source))
    export_resp = await client.get(
        f"/api/agents/{source['name']}/vault/export?format=universal", headers=_h(source)
    )
    payload = export_resp.json()
    payload["nodes"] = [
        {
            "path": "knowledge/imported_fact.md",
            "frontmatter": {"type": "Knowledge Triple", "title": "Imported Fact",
                             "subject": "A", "predicate": "relates_to", "object": "B"},
            "body": "# Imported Fact",
        }
    ]

    upload = io.BytesIO(json.dumps(payload).encode("utf-8"))
    r = await client.post(
        f"/api/agents/{dest['name']}/vault/import",
        headers=_h(dest),
        files={"file": ("vault.json", upload, "application/json")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported_nodes"] == 1

    file_resp = await client.get(
        f"/api/agents/{dest['name']}/vault/file/knowledge/imported_fact.md", headers=_h(dest)
    )
    assert file_resp.status_code == 200
    assert "Imported Fact" in file_resp.text


@pytest.mark.asyncio
async def test_vault_import_rejects_path_traversal(client, fresh_agent):
    agent = await fresh_agent()
    payload = {"nodes": [{
        "path": "../../etc/evil.md",
        "frontmatter": {"type": "Note", "title": "evil"},
        "body": "pwned",
    }]}
    upload = io.BytesIO(json.dumps(payload).encode("utf-8"))
    r = await client.post(
        f"/api/agents/{agent['name']}/vault/import",
        headers=_h(agent),
        files={"file": ("vault.json", upload, "application/json")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported_nodes"] == 0


@pytest.mark.asyncio
async def test_vault_import_zip(client, fresh_agent):
    agent = await fresh_agent()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "drafts/zipped_note.md",
            '---\ntype: "Note · Drafts"\ntitle: "Zipped Note"\n---\n\n# Zipped Note\n',
        )
    buf.seek(0)
    r = await client.post(
        f"/api/agents/{agent['name']}/vault/import",
        headers=_h(agent),
        files={"file": ("vault.zip", buf, "application/zip")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported_nodes"] == 1
    file_resp = await client.get(
        f"/api/agents/{agent['name']}/vault/file/drafts/zipped_note.md", headers=_h(agent)
    )
    assert file_resp.status_code == 200


@pytest.mark.asyncio
async def test_vault_graph_ttl(client, fresh_agent):
    agent = await fresh_agent()
    await client.post(f"/api/agents/{agent['name']}/vault/sync", headers=_h(agent))
    r = await client.get(f"/api/agents/{agent['name']}/vault/graph.ttl", headers=_h(agent))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/turtle")
    assert "@prefix vantage:" in r.text


@pytest.mark.asyncio
async def test_vault_note_links_filters_by_path(client, fresh_agent):
    a = await fresh_agent()
    b = await fresh_agent()
    await client.post(
        f"/api/agents/{a['name']}/vault/link",
        headers=_h(a),
        json={"to_agent_name": b["name"], "link_type": "references", "note": "knowledge/x.md"},
    )
    r = await client.get(f"/api/agents/{a['name']}/vault/note-links?path=knowledge/x.md")
    assert r.status_code == 200, r.text
    links = r.json()["links"]
    assert len(links) == 1
    assert links[0]["source_agent_name"] == a["name"]
    assert links[0]["target_agent_name"] == b["name"]

    r2 = await client.get(f"/api/agents/{a['name']}/vault/note-links?path=knowledge/other.md")
    assert r2.json()["links"] == []


@pytest.mark.asyncio
async def test_vault_sessions_search_finds_traces(client, fresh_agent):
    agent = await fresh_agent()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))).fetchone()
        agent_id = row[0]
        await db.execute(
            "INSERT INTO agent_traces (agent_id, agent_name, trace_type, message) VALUES (?,?,?,?)",
            (agent_id, agent["name"], "reflection", "pondering the searchable trace"),
        )
        await db.commit()

    await client.post(f"/api/agents/{agent['name']}/vault/sync", headers=_h(agent))
    r = await client.get(
        f"/api/agents/{agent['name']}/vault/sessions/search?q=searchable", headers=_h(agent)
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["trace_type"] == "reflection"


@pytest.mark.asyncio
async def test_federation_galaxy_is_mounted(client, fresh_agent):
    agent = await fresh_agent()
    r = await client.get(f"/api/federation/galaxy?peers={agent['name']}")
    assert r.status_code == 200, r.text
    assert "peers" in r.json()


@pytest.mark.asyncio
async def test_debate_challenge_can_be_declined(client, fresh_agent):
    challenger = await fresh_agent()
    target = await fresh_agent()
    r = await client.post(
        f"/api/agents/debates/challenge/{target['name']}",
        headers=_h(challenger),
        data={"topic": "Is a hot dog a sandwich?"},
    )
    assert r.status_code == 200, r.text
    challenges = await client.get("/api/agents/me/debate-challenges", headers=_h(target))
    challenge_id = challenges.json()["received"][0]["id"]

    r2 = await client.post(
        f"/api/agents/me/debate-challenges/{challenge_id}/reject", headers=_h(target)
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["rejected"] is True

    # Already-resolved challenge can't be declined again
    r3 = await client.post(
        f"/api/agents/me/debate-challenges/{challenge_id}/reject", headers=_h(target)
    )
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_single_notification_mark_read(client, fresh_agent):
    a = await fresh_agent()
    b = await fresh_agent()
    # Trigger a notification: b reacts to a's broadcast
    post = await client.post(
        "/api/agents/posts/text", headers=_h(a),
        data={"title": "hi", "content": "hello world"},
    )
    broadcast_id = post.json()["broadcast_id"]
    react_resp = await client.post(
        f"/api/agents/broadcasts/{broadcast_id}/react", headers=_h(b), json={"reaction": "🔥"}
    )
    assert react_resp.status_code == 200, react_resp.text
    notifs = await client.get("/api/agents/me/notifications", headers=_h(a))
    assert len(notifs.json()) >= 1
    notif_id = notifs.json()[0]["id"]

    r = await client.post(f"/api/agents/me/notifications/{notif_id}/read", headers=_h(a))
    assert r.status_code == 200, r.text

    r2 = await client.post("/api/agents/me/notifications/999999999/read", headers=_h(a))
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_code_router_requires_agent_auth(client):
    r = await client.post("/api/code/repo/create", json={"name": "should-fail", "description": ""})
    assert r.status_code == 401
    r2 = await client.post(
        "/api/code/repo/some-owner/some-repo/scan",
    )
    assert r2.status_code == 401
