"""The pipeline spine: one registry, live probes, and a recorded hop per step.

What these tests hold down:
  * the status endpoint reports every repo with a *probed* status, and a repo
    with no process is never reported live;
  * hops are recorded in pipeline_events and read back;
  * attestation only touches confirmed orders, is idempotent, and lands in
    Proof's own database (temp DB here, never the real one);
  * signal validation reflects the TradingOS schema contract.
"""
import json
import os
import socket

import pytest
import pytest_asyncio

from backend import pipeline, proof_bridge
from backend.db import get_db

# The Proof relay is a checkout on the host (/opt/ares/Proof), not a package this
# repo depends on. Where it is absent the attestation tests skip rather than fail:
# the bridge is exercised against the real checkout on the box.
_PROOF_PRESENT = os.path.isdir(proof_bridge.PROOF_RELAY_DIR)


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _h(agent):
    return {"X-Agent-Key": agent["api_key"]}


@pytest_asyncio.fixture(autouse=True)
async def _clean(client):
    async with get_db() as db:
        await db.execute("DELETE FROM pipeline_events")
        await db.commit()
    yield


@pytest.fixture
def proof_db(tmp_path, monkeypatch):
    p = str(tmp_path / "proof_relay.db")
    monkeypatch.setattr(proof_bridge, "PROOF_DB", p)
    return p


@pytest.mark.asyncio
async def test_status_reports_every_node_with_a_probed_state(client, registered_agent):
    r = await client.get("/api/pipeline/status", headers=_h(registered_agent))
    assert r.status_code == 200, r.text
    d = r.json()
    names = {n["name"] for n in d["nodes"]}
    assert {"Vantage", "Crucible", "Proof", "mycelium", "TradingOS"} <= names
    assert all(n["status"] for n in d["nodes"])
    # The hub probes itself over HTTP. Under an ASGI test client there is no
    # listener, so the honest expectation is "live if a socket is there,
    # dormant if not" — never a hardcoded claim either way.
    vantage = next(n for n in d["nodes"] if n["name"] == "Vantage")
    assert vantage["status"] == ("live" if _listening("127.0.0.1", 8001) else "dormant"), vantage
    # a repo with no caller is never reported live
    kanban = next(n for n in d["nodes"] if n["name"] == "kanban")
    assert kanban["status"] == "archived"
    blocked = [n for n in d["nodes"] if n["status"] == "blocked"]
    assert blocked and all(n["detail"] for n in blocked)
    assert d["datasets"] and d["flow"]


@pytest.mark.asyncio
async def test_hops_are_recorded_and_readable(client, registered_agent):
    async with get_db() as db:
        await pipeline.record_event(db, "Vantage", "order_created", ref="order:1",
                                    detail={"symbol": "SOL"})
        await db.commit()

    r = await client.get("/api/pipeline/events", headers=_h(registered_agent))
    assert r.status_code == 200, r.text
    evs = r.json()["events"]
    assert evs and evs[0]["node"] == "Vantage"
    assert evs[0]["kind"] == "order_created"
    assert evs[0]["detail"]["symbol"] == "SOL"


@pytest.mark.skipif(not _PROOF_PRESENT, reason="Proof checkout not on this host")
@pytest.mark.asyncio
async def test_attest_writes_into_proof_and_is_idempotent(client, registered_agent, proof_db):
    # Five confirmed orders: Proof only ranks an agent once its tier leaves
    # 'unverified', which needs 5 attestations and score >= 100.
    async with get_db() as db:
        aid = (await (await db.execute(
            "SELECT id FROM agents ORDER BY id DESC LIMIT 1")).fetchone())[0]
        for i in range(5):
            await db.execute(
                """INSERT INTO trading_orders
                   (agent_id, wallet_id, order_type, side, symbol, chain, quantity, price,
                    status, tx_hash, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (aid, None, "market", "buy", "SOL", "solana", 1.0, 100.0,
                 "confirmed", f"paper:test-{i}"))
        await db.commit()

    r = await client.post("/api/pipeline/attest", headers=_h(registered_agent),
                          json={"limit": 10})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["attested"] == 5, first
    assert os.path.exists(proof_db)

    # second run must not double-count
    r = await client.post("/api/pipeline/attest", headers=_h(registered_agent),
                          json={"limit": 10})
    second = r.json()
    assert second["attested"] == 0 and second["skipped"] == 5, second

    r = await client.get("/api/pipeline/reputation", headers=_h(registered_agent))
    rep = r.json()
    assert rep["available"] is True
    assert rep["leaderboard"], "attestation produced no reputation row"


@pytest.mark.skipif(not _PROOF_PRESENT, reason="Proof checkout not on this host")
@pytest.mark.asyncio
async def test_attest_ignores_unconfirmed_orders(client, registered_agent, proof_db):
    async with get_db() as db:
        # Orders from the previous test share this session-scoped DB; clear the
        # eligible set so this case measures only its own failed row.
        await db.execute("DELETE FROM trading_orders WHERE status = 'confirmed'")
        aid = (await (await db.execute(
            "SELECT id FROM agents ORDER BY id DESC LIMIT 1")).fetchone())[0]
        await db.execute(
            """INSERT INTO trading_orders
               (agent_id, wallet_id, order_type, side, symbol, chain, quantity, price,
                status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (aid, None, "market", "buy", "SOL", "solana", 1.0, 100.0,
             "failed"))
        await db.commit()

    r = await client.post("/api/pipeline/attest", headers=_h(registered_agent), json={})
    d = r.json()
    assert d["attested"] == 0
    assert d.get("reason") == "no confirmed orders"


@pytest.mark.asyncio
async def test_validate_signal_against_tradingos_schema(client, registered_agent):
    ok = await client.post("/api/pipeline/validate-signal", headers=_h(registered_agent),
                           json={"symbol": "SOL", "direction": "long", "conviction": 0.8,
                                 "source": "pumpfun_launch_radar", "mint": "Mint111"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["conforms"] is True
    assert body["schema"] == "tradingos.v1.SignalGenome"

    bad = await client.post("/api/pipeline/validate-signal", headers=_h(registered_agent),
                            json={"symbol": "", "direction": "sideways", "conviction": 4.2})
    b = bad.json()
    assert b["conforms"] is False
    assert len(b["problems"]) >= 3


@pytest.mark.asyncio
async def test_substrate_surface(client, registered_agent):
    r = await client.get("/api/pipeline/substrate", headers=_h(registered_agent))
    assert r.status_code == 200, r.text
    d = r.json()
    assert "gateway_db" in d
    # either it read traces, or it explained why not — never a silent zero
    assert "traces" in d or "error" in d


@pytest.mark.asyncio
async def test_remember_refuses_cleanly_without_a_key(client, registered_agent, monkeypatch, tmp_path):
    """No signing key must be a stated refusal, never a silent success."""
    from backend import minipae_bridge

    monkeypatch.setattr(minipae_bridge, "NSEC_FILE", str(tmp_path / "absent.nsec"))
    async with get_db() as db:
        aid = (await (await db.execute(
            "SELECT id FROM agents ORDER BY id DESC LIMIT 1")).fetchone())[0]
        cur = await db.execute(
            """INSERT INTO trading_orders
               (agent_id, wallet_id, order_type, side, symbol, chain, quantity, price,
                status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (aid, None, "market", "buy", "SOL", "solana", 1.0, 100.0, "confirmed"))
        await db.commit()
        oid = cur.lastrowid

    r = await client.post("/api/pipeline/remember", headers=_h(registered_agent),
                          json={"order_id": oid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "no signing key" in body["reason"]


@pytest.mark.asyncio
async def test_remember_unknown_order_is_404(client, registered_agent):
    r = await client.post("/api/pipeline/remember", headers=_h(registered_agent),
                          json={"order_id": 987654321})
    assert r.status_code == 404
