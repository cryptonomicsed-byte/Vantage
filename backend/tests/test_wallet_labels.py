"""P4-05: every label from its rule, with evidence; KOL needs a real wallet."""
import time

import pytest

from backend import curve_listener as cl
from backend import wallet_labels as wl
from backend.db import get_db


def _b(w, rank, slot=None, ts=None, sol=0.5, funder=None, sig=None):
    return {"wallet": w, "rank": rank, "entry_slot": slot, "entry_ts": ts, "sol": sol,
            "funded_by": funder, "signature": sig}


def test_derive_dev_sniper_bundler_insider_whale():
    buyers = [_b("DEV", 1, slot=100, sig="create"), _b("S1", 2, slot=101), _b("B1", 3, slot=105),
              _b("B2", 4, slot=105), _b("B3", 5, slot=105), _b("INS", 6, slot=200, funder="DEV"),
              _b("WH", 7, slot=300, sol=12.0), _b("LATE", 30, slot=105)]
    items = wl._derive_runner("M", buyers, 100, None, "create", set(), "curve")
    got = {(w, l) for w, l, _ in items}
    assert ("DEV", "dev") in got and ("S1", "sniper") in got
    assert {("B1", "bundler"), ("B2", "bundler"), ("B3", "bundler")} <= got
    assert ("LATE", "bundler") not in got  # outside the first 20
    assert ("INS", "insider") in got and ("WH", "whale") in got
    assert not any(w == "DEV" and l != "dev" for w, l, _ in items)
    ev = next(e for w, l, e in items if (w, l) == ("S1", "sniper"))
    assert ev == {"mint": "M", "slots_after_create": 1}


def test_derive_uses_seconds_without_slots_and_deployer_table():
    items = wl._derive_runner("M", [_b("X", 1, ts=1002), _b("Y", 2, ts=1100)], None, 1000, None, {"DEP"}, "rpc")
    got = {(w, l) for w, l, _ in items}
    assert ("DEP", "dev") in got and ("X", "sniper") in got and ("Y", "sniper") not in got


@pytest.mark.asyncio
async def test_put_merges_evidence():
    async with get_db() as db:
        await wl._ensure(db)
        await wl.put(db, [("W1", "whale", {"mint": "a"}), ("W1", "whale", {"mint": "b"}),
                          ("W1", "whale", {"mint": "a"}), ("W1", "notalabel", {})])
        await db.commit()
    w = await wl.wallet("W1")
    assert [l["label"] for l in w["labels"]] == ["whale"]
    assert w["labels"][0]["evidence"] == [{"mint": "a"}, {"mint": "b"}]


@pytest.mark.asyncio
async def test_global_bot_hub_and_kol_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(wl, "BOT_MINTS", 3)
    monkeypatch.setattr(wl, "HUB_FANOUT", 2)
    c = cl.connect()
    now = int(time.time())
    rows = [{"sig": f"b{i}", "ix": 0, "slot": 1, "block_time": now, "mint": f"MINT{i}", "wallet": "BOTW",
             "side": "buy", "sol": 1, "tokens": 1, "v_sol_reserves": 30, "v_token_reserves": 1e9,
             "mcap_usd": 1.0, "source": "rpc"} for i in range(3)]
    rows.append(dict(rows[0], sig="k", mint="MINTK", wallet="KOLW"))
    cl.write_batch(c, rows, set())
    async with get_db() as db:
        for ddl in ("CREATE TABLE IF NOT EXISTS wallet_edges (chain TEXT, address_a TEXT, address_b TEXT, role TEXT, "
                    "tx_count INTEGER, total_value REAL, first_seen TEXT, last_seen TEXT, "
                    "UNIQUE(chain, address_a, address_b, role))",
                    "CREATE TABLE IF NOT EXISTS social_wallet_links (id INTEGER PRIMARY KEY, platform TEXT, "
                    "username TEXT, wallet_address TEXT NOT NULL, chain TEXT DEFAULT 'solana', post_url TEXT, "
                    "post_excerpt TEXT, extracted_at TEXT, UNIQUE(platform, username, wallet_address))"):
            await db.execute(ddl)
        await db.execute("INSERT OR IGNORE INTO wallet_edges (chain, address_a, address_b, role, tx_count, "
                         "total_value, first_seen, last_seen) VALUES ('solana','HUB','a1','funded',1,0,'','')")
        await db.execute("INSERT OR IGNORE INTO wallet_edges (chain, address_a, address_b, role, tx_count, "
                         "total_value, first_seen, last_seen) VALUES ('solana','HUB','a2','funded',1,0,'','')")
        for addr in ("KOLW", "MINT1", "NEVERBOUGHT", "XYZpump"):
            await db.execute("INSERT OR IGNORE INTO social_wallet_links (platform, username, wallet_address) "
                             "VALUES ('x','caller',?)", (addr,))
        await db.commit()
        counts = await wl.label_global(db, now)
        labels = await wl.labels_for(db, ["BOTW", "HUB", "KOLW", "MINT1", "NEVERBOUGHT", "XYZpump"])
    assert labels.get("BOTW") == ["bot"] and labels.get("HUB") == ["funding_hub"]
    assert labels.get("KOLW") == ["kol"]  # linked AND bought
    assert "MINT1" not in labels and "NEVERBOUGHT" not in labels and "XYZpump" not in labels
    assert counts["kol"] == 1


@pytest.mark.asyncio
async def test_endpoints(client):
    r = await client.get("/api/intel/wallet-labels")
    assert r.status_code == 200 and set(r.json()["labels"]) == set(wl.LABELS)
    r = await client.get("/api/intel/wallets/NOPE/labels")
    assert r.status_code == 200 and r.json()["labels"] == []
