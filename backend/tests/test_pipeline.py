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


async def _clear_signals():
    """These tests share one session DB; each must see only its own signals."""
    async with get_db() as db:
        await db.execute("DELETE FROM signal_pool")
        await db.execute("DELETE FROM signal_promotions")
        await db.commit()


async def _signals(rows):
    """Insert pooled signals: (source, symbol, direction, conviction, age_s)."""
    import time as _t
    async with get_db() as db:
        for source, symbol, direction, conviction, age_s in rows:
            await db.execute(
                """INSERT INTO signal_pool (symbol, source, type, conviction, direction, detail, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (symbol, source, "test", conviction, direction, "{}", int(_t.time()) - age_s))
        await db.commit()


@pytest.mark.asyncio
async def test_promotable_collapses_a_repeating_call(client, registered_agent):
    """A producer repeating the same call every 90s is one intent, not four orders."""
    await _clear_signals()
    await _signals([("ogun_forge_v2", "BONK", "SELL", 1.0, 120),
                    ("ogun_forge_v2", "BONK", "SELL", 1.0, 60),
                    ("ogun_forge_v2", "BONK", "SELL", 1.0, 30),
                    ("dex_new", "GOOBLO", "buy", 0.9, 45)])
    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"min_conviction": 0.75, "max_age_min": 60, "limit": 10,
                                "allow_short": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False
    assert body["candidates"] == 2, body
    bonk = next(p for p in body["plan"] if p["symbol"] == "BONK")
    assert bonk["repeats"] == 3


@pytest.mark.asyncio
async def test_promote_apply_places_one_paper_order_and_never_duplicates(client, registered_agent, monkeypatch):
    # A promotion is an order, so it meets the same risk check: notional is only
    # verifiable against a quote. Pin one so this test measures promotion.
    from backend import market_sources

    async def _quote(symbol):
        return 100.0

    monkeypatch.setattr(market_sources, "resolve_price", _quote)
    await _clear_signals()
    await _signals([("dex_new", "GOOBLO", "buy", 0.95, 60)])
    rw = await client.post("/api/trading/wallets/generate", headers=_h(registered_agent),
                           json={"system": "bip39", "chain": "solana", "label": "promote-wallet"})
    assert rw.status_code in (200, 409), rw.text
    wid = rw.json()["id"] if rw.status_code == 200 else None
    if wid is None:
        lw = await client.get("/api/trading/wallets", headers=_h(registered_agent))
        rows = lw.json() if isinstance(lw.json(), list) else lw.json().get("wallets", [])
        wid = next(w["id"] for w in rows if w.get("label") == "promote-wallet")

    payload = {"apply": True, "wallet_id": wid, "quantity": 0.001,
               "min_conviction": 0.75, "max_age_min": 60, "limit": 1}
    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent), json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["placed"]) == 1, body
    assert body["placed"][0]["side"] == "buy"

    # The same pooled signal must not become a second order.
    r2 = await client.post("/api/pipeline/promote", headers=_h(registered_agent), json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["placed"] == [], r2.json()


@pytest.mark.asyncio
async def test_promote_apply_requires_a_wallet_and_a_size(client, registered_agent):
    await _clear_signals()
    await _signals([("dex_new", "GOOBLO", "buy", 0.95, 60)])
    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"apply": True, "quantity": 0.0})
    assert r.status_code == 422
    assert "wallet_id" in r.json()["detail"]


@pytest.mark.asyncio
async def test_promoting_a_group_consumes_every_repeat(client, registered_agent, monkeypatch):
    """Four repeats of one call must not become four orders across four cycles."""
    from backend import market_sources

    async def _quote(symbol):
        return 100.0

    monkeypatch.setattr(market_sources, "resolve_price", _quote)

    await _clear_signals()
    await _signals([("ogun_forge_v2", "BONK", "SELL", 1.0, 300),
                    ("ogun_forge_v2", "BONK", "SELL", 1.0, 240),
                    ("ogun_forge_v2", "BONK", "SELL", 1.0, 180),
                    ("ogun_forge_v2", "BONK", "SELL", 1.0, 120)])

    rw = await client.post("/api/trading/wallets/generate", headers=_h(registered_agent),
                           json={"system": "bip39", "chain": "solana", "label": "group-wallet"})
    wid = rw.json()["id"] if rw.status_code == 200 else None
    if wid is None:
        lw = await client.get("/api/trading/wallets", headers=_h(registered_agent))
        rows = lw.json() if isinstance(lw.json(), list) else lw.json().get("wallets", [])
        wid = next(w["id"] for w in rows if w.get("label") == "group-wallet")

    payload = {"apply": True, "wallet_id": wid, "quantity": 0.001,
               "min_conviction": 0.75, "max_age_min": 600, "limit": 10,
               "symbols": ["BONK"], "allow_short": True}
    first = await client.post("/api/pipeline/promote", headers=_h(registered_agent), json=payload)
    assert first.status_code == 200, first.text
    assert len(first.json()["placed"]) == 1, first.json()

    second = await client.post("/api/pipeline/promote", headers=_h(registered_agent), json=payload)
    assert second.json()["placed"] == [], second.json()


@pytest.mark.asyncio
async def test_sell_signals_are_skipped_unless_shorting_is_asked_for(client, registered_agent, monkeypatch):
    """Paper has no position to reduce; a pending SELL is swept by the engine."""
    from backend import market_sources

    async def _quote(symbol):
        return 100.0

    monkeypatch.setattr(market_sources, "resolve_price", _quote)

    await _clear_signals()
    await _signals([("ogun_forge_v2", "BONK", "SELL", 1.0, 120)])
    rw = await client.post("/api/trading/wallets/generate", headers=_h(registered_agent),
                           json={"system": "bip39", "chain": "solana", "label": "sell-wallet"})
    wid = rw.json()["id"] if rw.status_code == 200 else None
    if wid is None:
        lw = await client.get("/api/trading/wallets", headers=_h(registered_agent))
        rows = lw.json() if isinstance(lw.json(), list) else lw.json().get("wallets", [])
        wid = next(w["id"] for w in rows if w.get("label") == "sell-wallet")

    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"apply": True, "wallet_id": wid, "quantity": 0.001,
                                "min_conviction": 0.75, "max_age_min": 600, "limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["placed"] == [], body
    assert body["candidates"] == 0, body   # a SELL never competes unless asked for

    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"min_conviction": 0.75, "max_age_min": 600, "limit": 5,
                                "allow_short": True})
    assert r.json()["candidates"] == 1, r.json()


# ── ticker collisions: a signal with a mint trades that mint, never the ticker ─

_FAKE_BTC_MINT = "CZRtwCDJy4cKNvt" "VwtgGqeEGzLnCDJ" "dTkexgpEsMpump"   # a pump.fun "BTC"


@pytest.fixture
async def purge_fake_btc():
    """Mint orders here fill on the shared session agent; remove them afterwards
    so they cannot leak into another file's view of that agent's book."""
    yield
    async with get_db() as db:
        await db.execute("DELETE FROM trading_orders WHERE mint=?", (_FAKE_BTC_MINT,))
        await db.commit()


async def _mint_signal(conviction=0.95):
    import time as _t
    async with get_db() as db:
        await db.execute(
            """INSERT INTO signal_pool (symbol, source, type, conviction, direction, detail, ts, mint)
               VALUES ('BTC','ogun_degen','degen',?, 'BUY','Pump signal: +367%',?,?)""",
            (conviction, int(_t.time()) - 30, _FAKE_BTC_MINT))
        await db.commit()


async def _wallet_for(client, agent, label):
    rw = await client.post("/api/trading/wallets/generate", headers=_h(agent),
                           json={"system": "bip39", "chain": "solana", "label": label})
    if rw.status_code == 200:
        return rw.json()["id"]
    lw = await client.get("/api/trading/wallets", headers=_h(agent))
    rows = lw.json() if isinstance(lw.json(), list) else lw.json().get("wallets", [])
    return next(w["id"] for w in rows if w.get("label") == label)


@pytest.mark.asyncio
async def test_a_copycat_ticker_is_priced_by_its_mint_not_as_the_major(client, registered_agent, monkeypatch, purge_fake_btc):
    from backend import market_sources

    async def bitcoin(symbol):
        return 75_640.0            # what the ticker "BTC" resolves to

    async def by_mint(mint, chain="solana"):
        return 0.0002 if mint == _FAKE_BTC_MINT else None

    monkeypatch.setattr(market_sources, "resolve_price", bitcoin)
    monkeypatch.setattr(market_sources, "price_by_mint", by_mint)

    await _clear_signals()
    await _mint_signal()
    wid = await _wallet_for(client, registered_agent, "mint-wallet")
    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"apply": True, "wallet_id": wid, "quantity": 1000.0,
                                "min_conviction": 0.75, "max_age_min": 60, "limit": 1})
    assert r.status_code == 200, r.text
    placed = r.json()["placed"]
    assert len(placed) == 1, r.json()

    async with get_db() as db:
        row = await (await db.execute(
            "SELECT mint, avg_fill_price, status FROM trading_orders WHERE id=?",
            (placed[0]["order_id"],))).fetchone()
    assert row[0] == _FAKE_BTC_MINT
    assert row[1] == pytest.approx(0.0002), "entered at bitcoin's price"
    assert row[2] == "filled"


@pytest.mark.asyncio
async def test_no_mint_quote_means_no_order_never_a_ticker_fallback(client, registered_agent, monkeypatch, purge_fake_btc):
    from backend import market_sources

    async def bitcoin(symbol):
        return 75_640.0

    async def no_quote(mint, chain="solana"):
        return None

    monkeypatch.setattr(market_sources, "resolve_price", bitcoin)
    monkeypatch.setattr(market_sources, "price_by_mint", no_quote)

    await _clear_signals()
    await _mint_signal()
    wid = await _wallet_for(client, registered_agent, "mint-wallet-2")
    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"apply": True, "wallet_id": wid, "quantity": 1000.0,
                                "min_conviction": 0.75, "max_age_min": 60, "limit": 1})
    assert r.status_code == 200, r.text
    assert r.json()["placed"] == [], r.json()   # fail closed on an unpriceable mint
    assert r.json()["skipped"], r.json()


def test_price_by_mint_ignores_pairs_where_the_mint_is_the_quote_token(monkeypatch):
    import asyncio
    from backend import market_sources as ms

    mint = _FAKE_BTC_MINT
    payload = {"pairs": [
        # inverted: our mint is the quote token -- priceUsd is the OTHER token's
        {"chainId": "solana", "baseToken": {"address": "So1111"}, "quoteToken": {"address": mint},
         "priceUsd": "88.9", "liquidity": {"usd": 9_000_000}},
        {"chainId": "solana", "baseToken": {"address": mint}, "priceUsd": "0.00021",
         "liquidity": {"usd": 40_000}},
        {"chainId": "solana", "baseToken": {"address": mint}, "priceUsd": "0.00019",
         "liquidity": {"usd": 5_000}},
    ]}

    async def fake_get(url, timeout=8.0, retries=2):
        return payload

    monkeypatch.setattr(ms, "_get_json", fake_get)
    monkeypatch.setattr(ms, "_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(ms, "_cache_put", lambda *a, **k: None)
    assert asyncio.run(ms.price_by_mint(mint)) == pytest.approx(0.00021)



@pytest.mark.asyncio
async def test_a_mint_position_is_its_own_position_valued_by_its_mint(
        client, registered_agent, monkeypatch, purge_fake_btc):
    """Positions keyed by ticker merged a pump.fun "BTC" into real BTC and then
    valued the lot at bitcoin's price."""
    from backend import market_sources
    from backend.routers import trading as trading_router

    async def bitcoin(symbol):
        return 75_640.0

    async def by_mint(mint, chain="solana"):
        return 0.0003 if mint == _FAKE_BTC_MINT else None

    monkeypatch.setattr(market_sources, "resolve_price", bitcoin)
    monkeypatch.setattr(market_sources, "price_by_mint", by_mint)

    async with get_db() as db:
        # Its own agent: nothing here may touch another test's book.
        import uuid as _uuid
        cur = await db.execute("INSERT INTO agents (name, api_key) VALUES (?, ?)",
                               (f"book-{_uuid.uuid4().hex[:8]}", f"k-{_uuid.uuid4().hex}"))
        aid = cur.lastrowid
        for mint, qty, px in (("", 0.01, 75_000.0), (_FAKE_BTC_MINT, 1000.0, 0.0002)):
            await db.execute(
                """INSERT INTO trading_orders (agent_id, side, symbol, chain, quantity,
                        filled_quantity, avg_fill_price, status, mint, executed_at)
                   VALUES (?, 'BUY', 'BTC', 'solana', ?, ?, ?, 'filled', ?, datetime('now'))""",
                (aid, qty, qty, px, mint))
        await db.commit()

    valued = await trading_router._value_positions(await trading_router._load_book(aid))
    by_key = {(p["symbol"], p["mint"]): p for p in valued["positions"]}
    real, fake = by_key[("BTC", None)], by_key[("BTC", _FAKE_BTC_MINT)]
    assert real["net_quantity"] == pytest.approx(0.01)
    assert fake["net_quantity"] == pytest.approx(1000.0)
    assert fake["live_price"] == pytest.approx(0.0003)          # not 75,640
    assert fake["market_value_usd"] == pytest.approx(0.3)
    async with get_db() as db:
        await db.execute("DELETE FROM trading_orders WHERE agent_id=?", (aid,))
        await db.execute("DELETE FROM agents WHERE id=?", (aid,))
        await db.commit()



@pytest.mark.asyncio
async def test_one_noisy_producer_cannot_hide_every_other_source(client, registered_agent):
    """The limit used to apply to raw rows before grouping: four repeats of a
    1.0-conviction SELL filled every slot and a 0.9 BUY was never even read."""
    await _clear_signals()
    await _signals([("vantage-predictor", "SOL", "SELL", 1.0, 30 + i) for i in range(6)]
                   + [("dex_new", "BRUH", "buy", 1.0, 40)]
                   + [("ogun_degen", "WEALTH", "BUY", 0.9, 50)])
    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"min_conviction": 0.75, "max_age_min": 60, "limit": 2})
    plan = r.json()["plan"]
    assert [p["source"] for p in plan] == ["dex_new", "ogun_degen"], plan


@pytest.mark.asyncio
async def test_two_tokens_sharing_a_ticker_are_two_calls(client, registered_agent):
    import time as _t
    await _clear_signals()
    async with get_db() as db:
        for mint in ("MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"):
            await db.execute(
                """INSERT INTO signal_pool (symbol, source, type, conviction, direction, detail, ts, mint)
                   VALUES ('BTC','ogun_degen','degen',0.9,'BUY','{}',?,?)""",
                (int(_t.time()) - 20, mint))
        await db.commit()
    r = await client.post("/api/pipeline/promote", headers=_h(registered_agent),
                          json={"min_conviction": 0.75, "max_age_min": 60, "limit": 10})
    assert r.json()["candidates"] == 2, r.json()
    assert {p["mint"] for p in r.json()["plan"]} == {
        "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"}
