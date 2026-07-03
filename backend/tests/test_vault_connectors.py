"""External memory connectors: scoped tokens that let a third-party tool
stream conversation transcripts into an agent's memory vault."""
import pytest
import yaml

from backend.memory_vault import MemoryVault


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n")
    _, fm_block, _ = text.split("---", 2)
    return yaml.safe_load(fm_block) or {}


async def _create_connector(client, agent, name="Claude Code — laptop", source="claude-code"):
    r = await client.post(
        f"/api/agents/{agent['name']}/vault/external/connectors",
        headers=_h(agent),
        json={"name": name, "source": source},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_create_connector_returns_token_once_and_others_cant_see_it(client, fresh_agent):
    agent = await fresh_agent()
    other = await fresh_agent()
    conn = await _create_connector(client, agent)
    assert conn["token"].startswith("vconn_")
    assert conn["header"] == "X-Vault-Connector-Key"

    r = await client.get(f"/api/agents/{agent['name']}/vault/external/connectors", headers=_h(agent))
    assert r.status_code == 200, r.text
    connectors = r.json()["connectors"]
    assert any(c["id"] == conn["connector_id"] for c in connectors)
    assert all("token" not in c for c in connectors)

    r = await client.get(f"/api/agents/{agent['name']}/vault/external/connectors", headers=_h(other))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ingest_requires_valid_connector_key(client, fresh_agent):
    agent = await fresh_agent()
    r = await client.post(
        "/api/vault/external/ingest",
        headers={"X-Vault-Connector-Key": "vconn_not_a_real_token"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401

    r = await client.post("/api/vault/external/ingest", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_ingest_writes_okf_conformant_vault_note(client, fresh_agent):
    agent = await fresh_agent()
    conn = await _create_connector(client, agent, name="Grok bridge", source="grok")

    r = await client.post(
        "/api/vault/external/ingest",
        headers={"X-Vault-Connector-Key": conn["token"]},
        json={
            "conversation_id": "conv-1",
            "title": "Trading strategy chat",
            "messages": [
                {"role": "user", "content": "What's a good BTC: DCA strategy?"},
                {"role": "assistant", "content": "Consider weekly buys."},
            ],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["conversation_id"] == "conv-1"
    assert data["turn_count"] == 2

    import aiosqlite
    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))).fetchone()
    vault = MemoryVault(row[0], agent["name"])
    note_path = vault.vault_path / data["vault_path"]
    assert note_path.exists()
    fm = _frontmatter(note_path.read_text(encoding="utf-8"))
    assert fm["type"] == "Conversation · External · Grok"
    assert fm["node_kind"] == "star"
    assert fm["title"] == "Trading strategy chat"

    # The connector itself is also rendered as a vault node.
    connector_notes = list((vault.vault_path / "external").glob("connector-*.md"))
    assert connector_notes, "expected a rendered connector node"


@pytest.mark.asyncio
async def test_ingest_appends_to_same_conversation_and_galaxy_shows_edge(client, fresh_agent):
    agent = await fresh_agent()
    conn = await _create_connector(client, agent, name="Hermes CLI", source="hermes-cli")

    r1 = await client.post(
        "/api/vault/external/ingest",
        headers={"X-Vault-Connector-Key": conn["token"]},
        json={"conversation_id": "thread-9", "messages": [{"role": "user", "content": "turn one"}]},
    )
    assert r1.json()["turn_count"] == 1

    r2 = await client.post(
        "/api/vault/external/ingest",
        headers={"X-Vault-Connector-Key": conn["token"]},
        json={"conversation_id": "thread-9", "messages": [{"role": "assistant", "content": "turn two"}]},
    )
    assert r2.json()["turn_count"] == 2

    import aiosqlite
    from backend.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))).fetchone()
    vault = MemoryVault(row[0], agent["name"])
    data = vault.get_galaxy_data()
    ext_stars = [s for s in data["stars"] if s["constellation"] == "external-memory"]
    assert len(ext_stars) == 2  # connector node + conversation node
    ext_edges = [e for e in data["edges"] if e.get("predicate") == "captured"]
    assert len(ext_edges) == 1


@pytest.mark.asyncio
async def test_revoked_connector_can_no_longer_ingest(client, fresh_agent):
    agent = await fresh_agent()
    conn = await _create_connector(client, agent)

    r = await client.delete(
        f"/api/agents/{agent['name']}/vault/external/connectors/{conn['connector_id']}",
        headers=_h(agent),
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/vault/external/ingest",
        headers={"X-Vault-Connector-Key": conn["token"]},
        json={"messages": [{"role": "user", "content": "should be rejected"}]},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_ingest_rejects_empty_messages(client, fresh_agent):
    agent = await fresh_agent()
    conn = await _create_connector(client, agent)
    r = await client.post(
        "/api/vault/external/ingest",
        headers={"X-Vault-Connector-Key": conn["token"]},
        json={"messages": []},
    )
    assert r.status_code == 422
