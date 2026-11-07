"""trading_mode: paper always runs, live needs three gates, every transition is audited.

The behaviour under test is the split this module exists to enforce:

  * paper trading has no approval gate and cannot be switched off;
  * live requires armed + live + the global VANTAGE_LIVE_TRADING switch, and a
    missing global switch must refuse rather than silently fall through;
  * every arm/disarm/enable-live/disable-live leaves a durable audit row.

The mycelium emitters are exercised with _post_trace monkeypatched, so these
tests never write to a live substrate.
"""
import pytest

from backend import trading_mode as tm
from backend import mycelium_bridge as mb
from backend.db import get_db


# ── the three gate predicate ────────────────────────────────────────────────

@pytest.mark.parametrize("armed,live,global_on,expected,reason", [
    (True,  True,  True,  True,  ""),
    (True,  True,  False, False, "global_live_disabled"),
    (True,  False, True,  False, "strategy_live_gate_closed"),
    (True,  False, False, False, "strategy_live_gate_closed"),
    (False, True,  True,  False, "strategy_not_armed"),
    (False, False, False, False, "strategy_not_armed"),
])
def test_live_order_allowed(armed, live, global_on, expected, reason):
    allowed, got_reason = tm.live_order_allowed(
        armed=armed, live=live, global_enabled=global_on)
    assert allowed is expected
    assert got_reason == reason


def test_global_switch_defaults_off(monkeypatch):
    monkeypatch.delenv(tm.GLOBAL_LIVE_ENV, raising=False)
    assert tm.global_live_enabled() is False
    # and an explicit "0"/"" is still off
    for value in ("0", "", "false", "no", "off"):
        monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, value)
        assert tm.global_live_enabled() is False


def test_global_switch_on(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, value)
        assert tm.global_live_enabled() is True


# ── paper is unconditional ──────────────────────────────────────────────────

def test_paper_mode_is_always_on():
    assert tm.paper_mode_always_on() is True


def test_rs2_never_applies_to_paper():
    """The resolver/policy approval gate belongs to live only, if ever wired in."""
    assert tm.rs2_applies_to("paper") is False
    assert tm.rs2_applies_to("live") is True


def test_mode_for_strategy():
    assert tm.mode_for_strategy(1, 1) == "live"
    assert tm.mode_for_strategy(1, 0) == "paper"
    assert tm.mode_for_strategy(0, 1) == "paper"   # armed is the outer gate
    assert tm.mode_for_strategy(None, None) == "paper"


# ── audit ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mode_change_is_audited(client):
    async with get_db() as db:
        await db.execute("DELETE FROM trading_mode_audit")
        await db.commit()

    async with get_db() as db:
        await tm.record_mode_change(
            db, agent_id=7, scope="strategy", strategy_id=42,
            action="enable_live", prev_state="paper", new_state="live",
            note="test", actor="pytest",
        )

    async with get_db() as db:
        rows = await tm.recent_mode_changes(db, limit=5)

    assert rows, "audit row missing — a mode change that leaves no trace is untraceable"
    row = rows[0]
    assert row["action"] == "enable_live"
    assert row["scope"] == "strategy"
    assert row["strategy_id"] == 42
    assert row["agent_id"] == 7
    assert row["prev_state"] == "paper" and row["new_state"] == "live"
    assert row["ts"], "audit row has no timestamp"


@pytest.mark.asyncio
async def test_audit_failure_does_not_raise(client):
    """A broken audit table must not block a disarm — but must not be silent either."""

    class BrokenDB:
        async def execute(self, *a, **k):
            raise RuntimeError("no such table: trading_mode_audit")

        async def commit(self):
            raise RuntimeError("commit failed")

    # returns None, does not raise
    assert await tm.record_mode_change(
        BrokenDB(), agent_id=1, scope="strategy", action="disarm") is None


# ── mycelium emission (no network) ──────────────────────────────────────────

def test_emit_trade_decision_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(mb, "_post_trace", lambda body: captured.update(body) or True)

    ok = mb.emit_trade_decision(
        mode="live", symbol="SOL", side="buy", size_usd=12.5,
        quantity=0.05, price=250.0, strategy_id=3, order_id=99, chain="solana",
        reason="test",
    )

    assert ok is True
    assert captured["kind"] == "decision"
    assert captured["target"] == "solana:SOL"
    assert captured["action"] == "buy"
    assert captured["payload"]["mode"] == "live"
    assert captured["payload"]["strategy_id"] == 3
    assert captured["payload"]["size_usd"] == 12.5


def test_emit_trade_outcome_paper_is_marked_simulated(monkeypatch):
    captured = {}
    monkeypatch.setattr(mb, "_post_trace", lambda body: captured.update(body) or True)

    ok = mb.emit_trade_outcome(
        mode="paper", symbol="SOL", side="sell", order_id=1, quantity=1,
        fill_price=100.0, pnl_usd=2.5, tx_hash="paper:abc123", status="filled",
        chain="solana",
    )

    assert ok is True
    assert captured["kind"] == "outcome"
    assert captured["payload"]["mode"] == "paper"
    assert captured["payload"]["simulated"] is True
    assert captured["payload"]["tx_hash"] == "paper:abc123"


def test_emit_trade_outcome_live_is_not_simulated(monkeypatch):
    captured = {}
    monkeypatch.setattr(mb, "_post_trace", lambda body: captured.update(body) or True)

    mb.emit_trade_outcome(
        mode="live", symbol="SOL", side="buy", order_id=2, quantity=1,
        fill_price=100.0, tx_hash="5xRealSignature", status="submitted",
        chain="solana",
    )
    assert captured["payload"]["simulated"] is False
    assert captured["payload"]["mode"] == "live"


def test_emitters_never_raise_when_the_bridge_fails(monkeypatch):
    def boom(body):
        raise RuntimeError("substrate unreachable")

    monkeypatch.setattr(mb, "_post_trace", boom)
    # Telemetry must never break a trade.
    assert mb.emit_trade_decision(mode="paper", symbol="X", side="buy") is False
    assert mb.emit_trade_outcome(mode="paper", symbol="X", side="buy") is False
