"""Galaxy wallet data gaps: top_trader producer, first_buyer relabel,
runner-watchlist decoration on /api/moneyflow wallet nodes."""
import sqlite3
import time

import pytest

from backend import galaxy_roles as gr
from backend import runner_watchlist as rw
from backend.db import get_db

TWR = """CREATE TABLE IF NOT EXISTS token_wallet_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, symbol TEXT, wallet_address TEXT NOT NULL,
    role TEXT NOT NULL, rank INTEGER, metric REAL, metric_label TEXT,
    discovered_at TEXT DEFAULT (datetime('now')), UNIQUE(mint, wallet_address, role))"""


def _t(w, side, sol, tok):
    return {"wallet": w, "side": side, "sol": sol, "tokens": tok}


# ── top traders ──────────────────────────────────────────────────────

def test_rank_top_traders_pnl_dust_pool_and_buy_only():
    trades = [
        _t("WIN", "buy", 1.0, 1e6), _t("WIN", "sell", 3.0, 1e6),          # +2.0
        _t("HALF", "buy", 2.0, 2e6), _t("HALF", "sell", 1.5, 1e6),        # sold half: 1.5 - 1.0 = +0.5
        _t("LOSER", "buy", 2.0, 1e6), _t("LOSER", "sell", 1.0, 1e6),      # -1.0: not a top trader
        _t("HOLDER", "buy", 9.0, 9e6),                                   # realised nothing
        _t("DUSTY", "buy", 0.001, 1e3), _t("DUSTY", "sell", 0.009, 1e3),  # dust only
        _t("POOLX", "buy", 500.0, 2e8), _t("POOLX", "sell", 600.0, 1e6),  # liquidity seed -> pool
        _t("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "sell", 50.0, 1e6),
        _t("MINTX", "sell", 5.0, 1e6),
    ]
    top = gr.rank_top_traders(trades, mint="MINTX")
    assert [(r["wallet"], r["rank"]) for r in top] == [("WIN", 1), ("HALF", 2)]
    assert top[0]["label"] == "PnL +2 SOL · 2 trades" and top[0]["pnl"] == pytest.approx(2.0)
    assert top[1]["pnl"] == pytest.approx(0.5)


def test_rank_top_traders_caps_at_top_n():
    trades = []
    for i in range(15):
        trades += [_t(f"W{i:02d}", "buy", 1.0, 1e6), _t(f"W{i:02d}", "sell", 1.0 + (i + 1) / 10, 1e6)]
    top = gr.rank_top_traders(trades, top_n=10)
    assert len(top) == 10 and top[0]["wallet"] == "W14" and [r["rank"] for r in top] == list(range(1, 11))


@pytest.mark.asyncio
async def test_refresh_top_traders_from_curve_db(tmp_path, monkeypatch):
    cdb = tmp_path / "curve.db"
    c = sqlite3.connect(cdb)
    c.execute("CREATE TABLE curve_trades (sig TEXT, ix INTEGER, slot INTEGER, block_time INTEGER, mint TEXT, "
              "wallet TEXT, side TEXT, sol REAL, tokens REAL, v_sol_reserves REAL, v_token_reserves REAL, "
              "mcap_usd REAL, n_in_mint INTEGER, source TEXT, quote TEXT, PRIMARY KEY (sig, ix))")
    now = int(time.time())
    rows = [("s1", "TTMINT", "ALPHA", "buy", 1.0, 1e6, now - 600, "sol"),
            ("s2", "TTMINT", "ALPHA", "sell", 4.0, 1e6, now - 300, "sol"),
            ("s3", "TTMINT", "BETA", "buy", 1.0, 1e6, now - 600, "sol"),
            ("s4", "TTMINT", "BETA", "sell", 1.5, 1e6, now - 200, "sol"),
            ("s5", "TTMINT", "OLD", "buy", 1.0, 1e6, now - 3 * 86400, "sol"),   # outside window
            ("s6", "TTMINT", "OLD", "sell", 9.0, 1e6, now - 3 * 86400, "sol"),
            ("s7", "TTMINT", "OTHERQ", "buy", 1.0, 1e6, now - 600, "other"),    # non-SOL quote
            ("s8", "TTMINT", "OTHERQ", "sell", 9.0, 1e6, now - 500, "other")]
    c.executemany("INSERT INTO curve_trades (sig, ix, mint, wallet, side, sol, tokens, block_time, quote) "
                  "VALUES (?,0,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    monkeypatch.setenv("CURVE_DB", str(cdb))
    monkeypatch.setenv("GALAXY_TOP_TRADERS_ACTIVE_MINTS", "0")
    async with get_db() as db:
        await db.execute(TWR)
        await db.execute("DELETE FROM token_wallet_roles WHERE mint='TTMINT'")
        await db.executemany(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role, rank, metric, metric_label) "
            "VALUES ('TTMINT','TTS',?,?,?,?,?)",
            [("FB1", "first_buyer", 1, 1.0, "Entry #1 · 1 SOL"),
             ("DAEMON", "top_trader", 1, 7, "txn_count"),              # other producer: untouched
             ("STALE", "top_trader", 3, 0.2, "PnL +0.2 SOL · 2 trades")])  # ours, no longer top: removed
        await db.commit()
        out = await gr.refresh_top_traders(db)
        got = [tuple(r) for r in await (await db.execute(
            "SELECT wallet_address, rank, metric, metric_label, symbol FROM token_wallet_roles "
            "WHERE mint='TTMINT' AND role='top_trader' ORDER BY wallet_address")).fetchall()]
    assert out["mints"] == 1 and out["rows"] == 2 and out["removed"] == 1
    assert got == [("ALPHA", 1, 3.0, "PnL +3 SOL · 2 trades", "TTS"),
                   ("BETA", 2, 0.5, "PnL +0.5 SOL · 2 trades", "TTS"),
                   ("DAEMON", 1, 7.0, "txn_count", "TTS")]


def test_flags_default_off(monkeypatch):
    for f in ("GALAXY_TOP_TRADERS_ENABLED", "GALAXY_RELABEL_FIRST_BUYERS"):
        monkeypatch.delenv(f, raising=False)
    assert not gr.any_enabled()
    monkeypatch.setenv("GALAXY_TOP_TRADERS_ENABLED", "1")
    assert gr.top_traders_enabled() and gr.any_enabled() and not gr.relabel_enabled()


# ── first_buyer relabel ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_relabel_first_buyers_dust_rerank_unknown_and_idempotent():
    async with get_db() as db:
        await db.execute(TWR)
        await db.execute("DELETE FROM token_wallet_roles WHERE mint IN ('RLA','RLB','RLC')")
        await db.executemany(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role, rank, metric, metric_label) "
            "VALUES (?,?,?,'first_buyer',?,?,?)",
            [("RLA", "A", "D1", 1, 0.0015, "Entry rank #1"),       # dust
             ("RLA", "A", "R1", 2, 2.5, "Entry rank #2"),
             ("RLA", "A", "D2", 3, 0.0, "Entry rank #3"),          # dust
             ("RLA", "A", "R2", 4, 0.8, "Entry rank #4"),
             ("RLA", "A", "N1", 5, None, None),                    # size unknown
             ("RLB", "B", "T1", 2, 1783835656.0, "block_time"),    # metric is a timestamp
             ("RLB", "B", "T2", 1, 1783835600.0, "block_time"),
             ("RLC", "C", "OK1", 1, 1.2, "Entry #1 · 1.2 SOL"),    # already converted: untouched
             ("RLC", "C", "L1", 1, 0.5, "Entry rank #1")])         # legacy on same mint: ranks after
        await db.commit()
        out = await gr.relabel_first_buyers(db, batch_mints=1)
        got = {(m, w): (r, me, lbl) for m, w, r, me, lbl in await (await db.execute(
            "SELECT mint, wallet_address, rank, metric, metric_label FROM token_wallet_roles "
            "WHERE mint IN ('RLA','RLB','RLC') AND role='first_buyer'")).fetchall()}
        again = await gr.relabel_first_buyers(db)
    assert out == {"mints": 3, "deleted": 2, "relabelled": 6}
    assert got == {("RLA", "R1"): (1, 2.5, "Entry #1 · 2.5 SOL"),
                   ("RLA", "R2"): (2, 0.8, "Entry #2 · 0.8 SOL"),
                   ("RLA", "N1"): (3, None, "Entry #3 (size unknown)"),
                   ("RLB", "T2"): (1, None, "Entry #1 (size unknown)"),
                   ("RLB", "T1"): (2, None, "Entry #2 (size unknown)"),
                   ("RLC", "OK1"): (1, 1.2, "Entry #1 · 1.2 SOL"),
                   ("RLC", "L1"): (2, 0.5, "Entry #2 · 0.5 SOL")}
    assert again["mints"] == 0 and again["deleted"] == 0 and again["relabelled"] == 0


# ── watchlist decoration ─────────────────────────────────────────────

async def _seed_watch(db):
    for d in rw.DDL:
        await db.execute(d)
    from backend import wallet_labels
    await wallet_labels._ensure(db)
    await db.execute("DELETE FROM runner_watchlist_wallets WHERE wallet IN ('RADARW1','ENTW1','ORPHAN1')")
    await db.execute("DELETE FROM runner_watchlist WHERE entity='wallet:ENTW1'")
    await db.execute("DELETE FROM radar_wallets WHERE wallet='RADARW1'")
    await db.execute("INSERT INTO radar_wallets (wallet, conviction, tier, evidence, generated_at, loaded_at) "
                     "VALUES ('RADARW1', 0.8, 'A', NULL, NULL, 1)")
    await db.execute("INSERT INTO runner_watchlist (entity, wallets, score, runners, runner_mints, contributions, "
                     "labels, updated_at, radar_conviction) VALUES ('wallet:ENTW1','[\"ENTW1\"]',1.5,2,'[]','[]','[]',1,NULL)")
    await db.executemany("INSERT INTO runner_watchlist_wallets (wallet, entity) VALUES (?,?)",
                         [("RADARW1", "radar:RADARW1"), ("ENTW1", "wallet:ENTW1"), ("ORPHAN1", "gone:ORPHAN1")])
    await db.commit()


@pytest.mark.asyncio
async def test_watchlist_for_keeps_radar_only_wallets():
    async with get_db() as db:
        await _seed_watch(db)
        got = await rw.watchlist_for(db, ["RADARW1", "ENTW1", "ORPHAN1", "radarw1", "NOBODY"])
    assert got["RADARW1"] == {"entity": "radar:RADARW1", "score": 0.8, "source": "radar", "radar_conviction": 0.8}
    assert got["ENTW1"] == {"entity": "wallet:ENTW1", "score": 1.5, "source": "runner", "radar_conviction": None}
    assert set(got) == {"RADARW1", "ENTW1"}   # orphan mapping dropped; base58 is case-sensitive


@pytest.mark.asyncio
async def test_moneyflow_wallet_node_carries_watchlist(client, registered_agent):
    async with get_db() as db:
        await db.execute(TWR)
        await _seed_watch(db)
        await db.execute("DELETE FROM token_wallet_roles WHERE mint='WLMINT'")
        await db.executemany(
            "INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role, rank, metric, metric_label) "
            "VALUES ('WLMINT','WLT',?,'first_buyer',?,?,?)",
            [("RADARW1", 1, 1.0, "Entry #1 · 1 SOL"), ("ENTW1", 2, 0.5, "Entry #2 · 0.5 SOL")])
        await db.commit()
    r = await client.get("/api/moneyflow?hours=24&tier_lookups=0",
                         headers={"X-Agent-Key": registered_agent["api_key"]})
    assert r.status_code == 200, r.text
    nodes = {n["id"]: n for n in r.json()["nodes"]}
    assert nodes["wallet:RADARW1"]["watchlist"]["source"] == "radar"
    assert nodes["wallet:ENTW1"]["watchlist"]["score"] == 1.5
