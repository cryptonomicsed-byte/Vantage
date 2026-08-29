"""Tests for backend/backtest/shadow_account.py — retrospective self-behavior
mining + counterfactual attribution, adapted from HKUDS/Vibe-Trading (MIT)
per the 2026-08-29 HKUDS pattern audit. Uses synthetic TradeRecord lists
(unit-level, no DB) plus one real-DB round trip through
backend/routers/trading.py's GET /api/trading/shadow-account.
"""
import pandas as pd
import pytest

from backend.backtest.models import TradeRecord
from backend.backtest.shadow_account import (
    MIN_PROFITABLE_TRADES,
    build_shadow_report,
    compute_attribution,
    extract_shadow_profile,
)


def _trade(symbol, entry, hours, pnl, price=100.0):
    entry_time = pd.Timestamp(entry, tz="UTC")
    exit_time = entry_time + pd.Timedelta(hours=hours)
    exit_price = price + pnl / 10.0
    return TradeRecord(
        symbol=symbol, direction=1, entry_price=price, exit_price=exit_price,
        entry_time=entry_time, exit_time=exit_time, size=10, leverage=1.0,
        pnl=pnl, pnl_pct=pnl / 1000.0, exit_reason="signal", holding_bars=1,
        commission=0.0, entry_margin=1000.0, exit_margin=1000.0 + pnl,
    )


def _disciplined_and_noisy_trades():
    """8 disciplined winners (09:00 entry, 4h hold) + 5 noisy losers
    (22:00 entry, 30h hold) — same shape as the manual verification run."""
    trades = []
    for i in range(8):
        trades.append(_trade("SOL", f"2026-01-{i+1:02d}T09:00:00", 4, 50.0))
    for i in range(5):
        trades.append(_trade("SOL", f"2026-01-{i+1:02d}T22:00:00", 30, -100.0))
    return trades


class TestExtraction:
    def test_raises_on_insufficient_profitable_trades(self):
        trades = _disciplined_and_noisy_trades()[:2]
        with pytest.raises(ValueError, match="Insufficient profitable trades"):
            extract_shadow_profile(trades, agent_id=1)

    def test_extracts_at_least_one_rule_from_disciplined_pattern(self):
        profile = extract_shadow_profile(_disciplined_and_noisy_trades(), agent_id=1)
        assert profile.profitable_trades == 8
        assert profile.total_trades == 13
        assert len(profile.rules) >= 1
        rule = profile.rules[0]
        assert rule.support_count >= 3
        assert 0 < rule.coverage_rate <= 1.0
        assert rule.human_text  # non-empty, human-readable

    def test_degenerate_fallback_at_exactly_min_support(self):
        # Exactly MIN_PROFITABLE_TRADES profitable trades, all identical
        # shape -- should still produce exactly one usable rule, not crash.
        trades = [_trade("SOL", f"2026-01-{i+1:02d}T09:00:00", 4, 50.0)
                  for i in range(MIN_PROFITABLE_TRADES)]
        profile = extract_shadow_profile(trades, agent_id=1, min_support=3)
        assert len(profile.rules) >= 1


class TestAttribution:
    def test_shadow_beats_real_when_noisy_trades_present(self):
        trades = _disciplined_and_noisy_trades()
        profile = extract_shadow_profile(trades, agent_id=1)
        attr = compute_attribution(trades, profile)

        # Shadow (rule-compliant subset) should retain the winners' PnL;
        # real includes the noisy losers dragging it down.
        assert attr.shadow_pnl == pytest.approx(400.0)
        assert attr.real_pnl == pytest.approx(-100.0)
        assert attr.delta_pnl == pytest.approx(500.0)
        # noise_trades_pnl should be positive (shadow avoids real losses)
        assert attr.noise_trades_pnl > 0
        # explained + missed must reconcile exactly to delta (residual invariant)
        explained = (
            attr.noise_trades_pnl + attr.early_exit_pnl
            + attr.late_exit_pnl + attr.overtrading_pnl
        )
        assert attr.missed_signals_pnl == pytest.approx(attr.delta_pnl - explained, abs=0.01)

    def test_counterfactual_trades_sorted_by_impact_descending(self):
        trades = _disciplined_and_noisy_trades()
        profile = extract_shadow_profile(trades, agent_id=1)
        attr = compute_attribution(trades, profile)
        impacts = [abs(t["impact"]) for t in attr.counterfactual_trades]
        assert impacts == sorted(impacts, reverse=True)
        assert len(attr.counterfactual_trades) <= 5

    def test_perfectly_disciplined_history_has_zero_noise(self):
        # Every trade matches the single extracted rule exactly -> no
        # rule-violating trades -> noise_trades_pnl must be 0.
        trades = [_trade("SOL", f"2026-01-{i+1:02d}T09:00:00", 4, 50.0)
                  for i in range(MIN_PROFITABLE_TRADES + 2)]
        profile = extract_shadow_profile(trades, agent_id=1, min_support=3, max_rules=1)
        attr = compute_attribution(trades, profile)
        assert attr.noise_trades_pnl == pytest.approx(0.0)
        assert attr.shadow_pnl == pytest.approx(attr.real_pnl)


@pytest.mark.asyncio
async def test_build_shadow_report_end_to_end_against_real_db(tmp_path, monkeypatch):
    """Full path: real trading_orders rows -> vantage_adapter.load_trade_records
    -> extract_shadow_profile -> compute_attribution, through the same
    build_shadow_report() the API route calls."""
    from backend.config import settings
    import backend.db as db_mod

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "vantage.db")

    await db_mod.init_agents_db()
    async with db_mod.get_db() as db:
        await db.execute("INSERT INTO agents (id, name, api_key) VALUES (1, 'test-agent', 'k')")
        oid = 1
        for i in range(8):
            entry = f"2026-01-{i+1:02d}T09:00:00"
            exit_ = f"2026-01-{i+1:02d}T13:00:00"
            await db.execute(
                "INSERT INTO trading_orders (id,agent_id,side,symbol,chain,quantity,"
                "filled_quantity,avg_fill_price,status,created_at,executed_at) "
                "VALUES (?,1,'BUY','SOL','solana',10,10,100,'filled',?,?)",
                (oid, entry, entry),
            )
            oid += 1
            await db.execute(
                "INSERT INTO trading_orders (id,agent_id,side,symbol,chain,quantity,"
                "filled_quantity,avg_fill_price,status,created_at,executed_at) "
                "VALUES (?,1,'SELL','SOL','solana',10,10,105,'filled',?,?)",
                (oid, exit_, exit_),
            )
            oid += 1
        await db.commit()

    report = await build_shadow_report(1)
    assert report["profile"]["profitable_trades"] == 8
    assert report["attribution"]["real_pnl"] == pytest.approx(400.0)
    assert report["attribution"]["shadow_pnl"] == pytest.approx(400.0)
