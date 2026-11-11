"""P0 live-trading gate: Sentinel operators, sign-off cards, the three live
layers, demo mode, deny-lists and the Èṣù stub.

The rules under test:
  * an agent key can never change the instance switch or arm a signer
  * turning live on / arming files a card; the requester cannot approve it;
    an `operator` cannot approve trading; owner/security can
  * Èṣù unconfigured = halted, nothing applied (outside demo)
  * braking (off / disarm) is immediate and never gated
  * Layer 0: unset env is the brake, the env can no longer force live on
  * Layer 1: losing or changing a strategy's wallet drops it to paper
  * Layer 2: the account kill switch revokes live for all strategies
  * demo: brake reads open, real settlement is refused everywhere, and a
    switch set in demo reads off once the instance leaves demo
"""
import hashlib
import secrets

import pytest
import pytest_asyncio

from backend import esu_gate
from backend import trading_mode as tm
from backend.config import settings
from backend.db import get_db

pytestmark = pytest.mark.asyncio
ADMIN = {"X-Admin-Key": "a" * 32}
ARM_KEY = "jupiter_signer_trading_enabled"


async def _human(role=None):
    token = secrets.token_hex(16)
    async with get_db() as db:
        cur = await db.execute("INSERT INTO humans (email, password_hash, display_name) VALUES (?,?,?)",
                               (f"{token}@t.test", "x", f"H{token[:6]}"))
        hid = cur.lastrowid
        await db.execute("INSERT INTO human_sessions (human_id, token_hash, expires_at) VALUES (?,?,datetime('now','+1 day'))",
                         (hid, hashlib.sha256(token.encode()).hexdigest()))
        await db.commit()
    return {"id": hid, "role": role, "h": {"X-Human-Session": token}}


@pytest_asyncio.fixture
async def ops(client):
    out = {}
    for name, role in (("requester", "operator"), ("operator", "operator"), ("security", "security"), ("owner", "owner")):
        h = await _human(role)
        r = await client.post("/api/admin/sentinel/operators", headers=ADMIN, json={"human_id": h["id"], "role": role})
        assert r.status_code == 200, r.text
        out[name] = h
    out["stranger"] = await _human()
    return out


@pytest_asyncio.fixture(autouse=True)
async def _clean(client, monkeypatch):
    monkeypatch.setattr(settings, "TOOL_TRADING", "test-tool-key", raising=False)
    monkeypatch.setattr(settings, "ESU_GATE_URL", "", raising=False)
    monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
    monkeypatch.setattr(settings, "TRADING_DENYLIST", "", raising=False)
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)

    async def reset():
        async with get_db() as db:
            await db.execute("UPDATE live_mode SET enabled=0, set_in_demo=0")
            await db.execute("DELETE FROM signoff_requests")
            await db.execute("DELETE FROM daemon_settings WHERE key=?", (ARM_KEY,))
            await db.commit()
        tm.invalidate_cache()
    await reset()
    yield
    await reset()


def _approve_esu(monkeypatch):
    async def ok(op):
        return esu_gate.GatekeeperResult(approved=True)
    monkeypatch.setattr(esu_gate, "evaluate", ok)


# ── Layer 0 ──────────────────────────────────────────────────────────────

async def test_unset_env_is_the_brake_and_env_cannot_force_live(monkeypatch):
    async with get_db() as db:
        await db.execute("UPDATE live_mode SET enabled=1")
        await db.commit()
    tm.invalidate_cache()
    assert tm.global_live_enabled() is False          # switch on, ceiling closed
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "true")
    tm.invalidate_cache()
    assert tm.global_live_enabled() is True           # ceiling open + switch on
    async with get_db() as db:
        await db.execute("UPDATE live_mode SET enabled=0")
        await db.commit()
    tm.invalidate_cache()
    assert tm.global_live_enabled() is False          # env alone never forces live


# ── operator gate, sign-off, no self-approval, Èṣù ──────────────────────

async def test_agent_key_cannot_touch_the_switch_or_arm(client, registered_agent):
    h = {"X-Agent-Key": registered_agent["api_key"]}
    assert (await client.put("/api/trading/live-mode", headers=h, json={"enabled": True})).status_code == 401
    assert (await client.put(f"/api/trading/daemon-settings/{ARM_KEY}", headers=h, json={"value": "1"})).status_code == 401


async def test_non_operator_human_is_refused(client, ops):
    r = await client.put("/api/trading/live-mode", headers=ops["stranger"]["h"], json={"enabled": False})
    assert r.status_code == 403


async def test_turning_on_files_a_card_and_only_a_different_approver_applies_it(client, ops, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    r = await client.put("/api/trading/live-mode", headers=ops["requester"]["h"], json={"enabled": True, "note": "go"})
    assert r.status_code == 202, r.text
    card = r.json()["signoff"]
    assert card["status"] == "pending" and r.json()["enabled"] is False
    # Same request again reuses the card.
    again = await client.put("/api/trading/live-mode", headers=ops["requester"]["h"], json={"enabled": True, "note": "go"})
    assert again.json()["signoff"]["id"] == card["id"]

    url = f"/api/sentinel/signoffs/{card['id']}/approve"
    assert (await client.post(url, headers=ops["requester"]["h"])).status_code == 403   # self-approval
    assert (await client.post(url, headers=ops["operator"]["h"])).status_code == 403    # not an approver role

    # Èṣù unconfigured: the stub halts, nothing changes.
    r = await client.post(url, headers=ops["security"]["h"])
    assert r.status_code == 200 and r.json()["status"] == "halted" and "esu_unavailable" in r.json()["result"]
    tm.invalidate_cache()
    assert tm.global_live_enabled() is False

    # A fresh card, cleared by the gate, is applied.
    _approve_esu(monkeypatch)
    card2 = (await client.put("/api/trading/live-mode", headers=ops["requester"]["h"], json={"enabled": True})).json()["signoff"]
    r = await client.post(f"/api/sentinel/signoffs/{card2['id']}/approve", headers=ops["owner"]["h"])
    assert r.json()["status"] == "approved", r.text
    tm.invalidate_cache()
    assert tm.global_live_enabled() is True
    async with get_db() as db:
        rows = await tm.recent_mode_changes(db, limit=1)
    assert rows[0]["action"] == "live_mode_on" and f"approved_by_human:{ops['owner']['id']}" in rows[0]["actor"]

    # Off is immediate for any operator, no card.
    r = await client.put("/api/trading/live-mode", headers=ops["operator"]["h"], json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False


async def test_on_refused_while_ceiling_closed(client, ops):
    r = await client.put("/api/trading/live-mode", headers=ops["requester"]["h"], json={"enabled": True})
    assert r.status_code == 409 and "ceiling" in r.text


async def test_denied_card_changes_nothing(client, ops, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    card = (await client.put("/api/trading/live-mode", headers=ops["requester"]["h"], json={"enabled": True})).json()["signoff"]
    r = await client.post(f"/api/sentinel/signoffs/{card['id']}/deny", headers=ops["operator"]["h"], json={"note": "no"})
    assert r.json()["status"] == "denied"
    assert (await client.post(f"/api/sentinel/signoffs/{card['id']}/approve", headers=ops["owner"]["h"])).status_code == 409


async def test_arming_a_signer_needs_a_card_disarm_is_immediate_denylist_never_arms(client, ops, monkeypatch):
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    _approve_esu(monkeypatch)
    async with get_db() as db:
        await db.execute("UPDATE live_mode SET enabled=1")
        await db.commit()
    tm.invalidate_cache()

    r = await client.put(f"/api/trading/daemon-settings/{ARM_KEY}", headers=ops["requester"]["h"], json={"value": "1"})
    assert r.status_code == 202, r.text
    sid = r.json()["signoff"]["id"]
    assert (await client.post(f"/api/sentinel/signoffs/{sid}/approve", headers=ops["security"]["h"])).json()["status"] == "approved"
    tool = {"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": "test-tool-key"}
    assert (await client.get(f"/api/trading/daemon-settings/{ARM_KEY}", headers=tool)).json()["value"] == "1"

    r = await client.put(f"/api/trading/daemon-settings/{ARM_KEY}", headers=ops["operator"]["h"], json={"value": "0"})
    assert r.status_code == 200 and r.json()["value"] == "0"

    monkeypatch.setattr(settings, "TRADING_DENYLIST", "jupiter_signer, freqtrade", raising=False)
    r = await client.put(f"/api/trading/daemon-settings/{ARM_KEY}", headers=ops["requester"]["h"], json={"value": "1"})
    assert r.status_code == 403
    async with get_db() as db:
        await db.execute("UPDATE daemon_settings SET value='1' WHERE key=?", (ARM_KEY,))
        await db.commit()
    body = (await client.get(f"/api/trading/daemon-settings/{ARM_KEY}", headers=tool)).json()
    assert body["value"] == "0" and body["stored"] == "1"


async def test_esu_stub_denies_by_default_and_on_error(monkeypatch):
    op = esu_gate.Operation(tool="t", params="{}", intent="x")
    assert (await esu_gate.evaluate(op)).is_approved() is False
    monkeypatch.setattr(settings, "ESU_GATE_URL", "http://127.0.0.1:9/never", raising=False)
    res = await esu_gate.evaluate(op)
    assert res.is_approved() is False and res.halt_reason().startswith("esu_error")


# ── Layers 1 and 2 ───────────────────────────────────────────────────────

async def _agent(fresh_agent):
    a = await fresh_agent()
    async with get_db() as db:
        (aid,) = await (await db.execute("SELECT id FROM agents WHERE name=?", (a["name"],))).fetchone()
    return {**a, "id": aid}


async def _strategy(agent_id, live=1):
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO trading_wallets (agent_id, label, chain, address) VALUES (?,?,?,?)",
            (agent_id, f"w{secrets.token_hex(4)}", "solana", f"addr{secrets.token_hex(6)}"))
        wid = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO trading_strategies (agent_id, name, strategy_type, wallet_id, enabled, armed, live) "
            "VALUES (?,?,?,?,1,1,?)", (agent_id, "s", "scalper_5020", wid, live))
        await db.commit()
        return wid, cur.lastrowid


async def test_layer1_losing_or_changing_the_wallet_drops_to_paper(client, fresh_agent):
    agent = await _agent(fresh_agent)
    h = {"X-Agent-Key": agent["api_key"]}
    wid, sid = await _strategy(agent["id"])
    r = await client.delete(f"/api/trading/wallets/{wid}", headers=h)
    assert r.json()["strategies_dropped_to_paper"] == 1
    async with get_db() as db:
        live, w = await (await db.execute("SELECT live, wallet_id FROM trading_strategies WHERE id=?", (sid,))).fetchone()
    assert live == 0 and w is None

    wid, sid = await _strategy(agent["id"])
    wid2, _ = await _strategy(agent["id"], live=0)
    r = await client.patch(f"/api/trading/strategies/{sid}", headers=h, json={"wallet_id": wid2})
    assert r.status_code == 200, r.text
    assert r.json()["live"] == 0


async def test_layer2_kill_switch_revokes_all_live(client, fresh_agent, monkeypatch):
    agent = await _agent(fresh_agent)
    h = {"X-Agent-Key": agent["api_key"]}
    _, sid = await _strategy(agent["id"])
    assert (await client.get("/api/trading/live-killswitch", headers=h)).json()["engaged"] is False
    r = await client.put("/api/trading/live-killswitch", headers=h, json={"engaged": True})
    assert r.status_code == 200 and tm.kill_switch_engaged(agent["id"]) is True
    async with get_db() as db:
        (live,) = await (await db.execute("SELECT live FROM trading_strategies WHERE id=?", (sid,))).fetchone()
    assert live == 0
    ok, why = tm.live_order_allowed(armed=True, live=True, wallet_bound=True,
                                    kill_engaged=tm.kill_switch_engaged(agent["id"]), global_enabled=True)
    assert not ok and why == "account_live_revoked"
    # Releasing re-enables nothing by itself.
    await client.put("/api/trading/live-killswitch", headers=h, json={"engaged": False})
    async with get_db() as db:
        (live,) = await (await db.execute("SELECT live FROM trading_strategies WHERE id=?", (sid,))).fetchone()
    assert live == 0


@pytest.mark.filterwarnings("ignore")
async def test_predicate_defaults_are_closed():
    assert tm.live_order_allowed(armed=True, live=True, global_enabled=True) == (False, "strategy_wallet_unbound")
    assert tm.live_order_allowed(armed=True, live=True, wallet_bound=True, global_enabled=True) == \
        (False, "account_live_revoked")
    assert tm.live_order_allowed(armed=True, live=True, wallet_bound=False, kill_engaged=False,
                                 global_enabled=True) == (False, "strategy_wallet_unbound")
    assert tm.mode_for_strategy(True, True) == "paper"
    assert tm.mode_for_strategy(True, True, wallet_bound=True, kill_engaged=False) == "live"


# ── demo mode ────────────────────────────────────────────────────────────

async def test_demo_keeps_the_brake_open_but_refuses_settlement(client, ops, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True, raising=False)
    assert tm.env_ceiling_open() is True and tm.settlement_allowed() is False
    status = tm.live_mode_status()
    assert status["demo_mode"] is True and status["settlement_allowed"] is False

    # Live can be switched on for testing (still via a card); Èṣù not cleared is noted, not blocking.
    card = (await client.put("/api/trading/live-mode", headers=ops["requester"]["h"], json={"enabled": True})).json()["signoff"]
    r = await client.post(f"/api/sentinel/signoffs/{card['id']}/approve", headers=ops["owner"]["h"])
    assert r.json()["status"] == "approved" and "settlement refused" in r.json()["result"]
    tm.invalidate_cache()
    assert tm.global_live_enabled() is True
    assert tm.live_order_allowed(armed=True, live=True, wallet_bound=True, kill_engaged=False) == \
        (False, "demo_mode_settlement_refused")

    # Standalone signers never read armed in demo.
    async with get_db() as db:
        await db.execute("INSERT OR REPLACE INTO daemon_settings (key, value) VALUES (?, '1')", (ARM_KEY,))
        await db.commit()
    tool = {"X-Vantage-Tool": "trading", "X-Vantage-Tool-Key": "test-tool-key"}
    assert (await client.get(f"/api/trading/daemon-settings/{ARM_KEY}", headers=tool)).json()["value"] == "0"

    # Leaving demo: the demo-set switch reads OFF even with the ceiling open.
    monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    tm.invalidate_cache()
    assert tm.global_live_enabled() is False


async def test_demo_refuses_sui_settlement(client, registered_agent, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True, raising=False)
    r = await client.post("/api/receipts/nope/settle/sui", headers={"X-Agent-Key": registered_agent["api_key"]})
    assert r.status_code == 423


async def test_daemon_denylist_refuses_start_before_systemctl(client, ops, monkeypatch):
    from backend.routers import daemons as d

    async def never(*a, **k):
        raise AssertionError("systemctl must not run for a deny-listed unit")
    monkeypatch.setattr(d, "_systemctl", never)
    monkeypatch.setattr(settings, "DAEMON_UNIT_PREFIX", "ares-", raising=False)
    monkeypatch.setattr(settings, "DAEMON_DENYLIST", "ares-freqtrade, ares-wallet-learner", raising=False)
    for verb in ("start", "restart"):
        r = await client.post(f"/api/admin/daemons/ares-wallet-learner.service/{verb}", headers=ops["owner"]["h"])
        assert r.status_code == 403, r.text
