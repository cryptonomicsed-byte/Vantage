"""Confirmed-block whale scan (WHALE_BLOCK_SCAN_ENABLED)."""
import pytest

from backend import btc_whales as bw
from backend import market_sources as ms


def _tx(txid, outs, coinbase=False, fee=1000, weight=800):
    return {"txid": txid, "fee": fee, "weight": weight,
            "vin": [{"is_coinbase": coinbase}],
            "vout": [{"value": v, "scriptpubkey_address": f"bc1q{txid}{k}"} for k, v in enumerate(outs)]}


def test_summarize_excludes_coinbase_and_sums_outputs():
    assert bw.summarize_tx(_tx("cb", [625_000_000], coinbase=True)) is None
    r = bw.summarize_tx(_tx("a", [150_000_000_00, 50_000_000]))
    assert r["value_btc"] == 150.5 and r["largest_output_btc"] == 150.0
    assert r["largest_output_address"] == "bc1qa0" and r["outputs"] == 2 and r["vsize"] == 200


def test_top_by_value_orders_largest_first():
    rows = [bw.summarize_tx(_tx(str(i), [i * 100_000_000])) for i in range(1, 40)]
    top = bw.top_by_value(rows, 5)
    assert [r["value_btc"] for r in top] == [39.0, 38.0, 37.0, 36.0, 35.0]


@pytest.mark.asyncio
async def test_scan_block_pages_all_txs(monkeypatch):
    monkeypatch.setattr(bw, "RPS", 1000.0)
    pages = {0: [_tx(f"t{i}", [i * 1_000_000]) for i in range(25)],
             25: [_tx(f"u{i}", [i * 10_000_000_000]) for i in range(5)] + [_tx("cb", [1], coinbase=True)]}

    async def fake_get(_c, path):
        if path.endswith("/txs/0"):
            return pages[0]
        if path.endswith("/txs/25"):
            return pages[25]
        return {"tx_count": 31, "height": 900000, "timestamp": 1794000000}

    monkeypatch.setattr(bw, "_get", fake_get)
    b = await bw.scan_block(None, "h")
    assert b["tx_count"] == 31 and b["scanned"] == 30 and b["top"][0]["txid"] == "u4"


def test_snapshot_filters_and_flags(monkeypatch):
    monkeypatch.setenv("WHALE_BLOCK_SCAN_ENABLED", "0")
    monkeypatch.setattr(bw, "_blocks", [{"height": 1, "hash": "h", "time": 5, "tx_count": 2, "scanned": 2,
                                          "top": [{"txid": "x", "value_btc": 500.0}, {"txid": "y", "value_btc": 2.0}]}])
    s = bw.snapshot(min_value_btc=100)
    assert s["enabled"] is False and [r["txid"] for r in s["transactions"]] == ["x"]
    assert s["transactions"][0]["block_height"] == 1


@pytest.mark.asyncio
async def test_mempool_rows_keep_full_txid(monkeypatch):
    ms._cache.pop("whales:raw", None)

    async def fake_json(url, timeout=8, retries=2):
        return [{"txid": "f" * 64, "value": 123_000_000, "fee": 400, "vsize": 200}]

    monkeypatch.setattr(ms, "_get_json", fake_json)
    rows = await ms.whale_txs(10)
    ms._cache.pop("whales:raw", None)
    assert rows[0]["txid"].endswith("…") and rows[0]["txid_full"] == "f" * 64
