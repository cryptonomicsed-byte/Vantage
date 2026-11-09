"""P1-08: ledger rows are mirrored to Mycelium as signal_record traces.

Acceptance: rows appear in GET :8811/api/traces?agent=vantage_ledger; a
Mycelium outage leaves rows unmirrored, then catches up. Plus: a retry after
a lost acknowledgement never doubles a trace (idempotent on target), the
token address survives while evidence wallets are scrubbed, and the flag
gates the loop. A local fake gateway stands in for :8811 with the real
gateway's contract (server-assigned ids, agent/action/since filters).
"""
import http.server
import json
import random
import threading
import time
import urllib.parse

import pytest

from backend import signal_ledger as sl

MINT = "So11111111111" + "111111111111111111111111111112"  # secret-scan:allow (wrapped SOL mint, public constant)
WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"  # secret-scan:allow (public example wallet, test only)


class _Gateway(http.server.BaseHTTPRequestHandler):
    traces: list = []
    up = True

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if not _Gateway.up:
            return self._send(503, {"error": "down"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body["id"] = f"t{len(_Gateway.traces)}"
        body["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _Gateway.traces.append(body)
        self._send(201, {"status": "ok", "id": body["id"]})

    def do_GET(self):
        if not _Gateway.up:
            return self._send(503, {"error": "down"})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rows = [t for t in _Gateway.traces
                if t["agent"] == q.get("agent", [t["agent"]])[0]
                and t["action"] == q.get("action", [t["action"]])[0]
                and t["ts"] >= q.get("since", [""])[0]]
        self._send(200, {"count": len(rows), "traces": rows or None})

    def log_message(self, *a):
        pass


@pytest.fixture
def gateway(monkeypatch):
    _Gateway.traces, _Gateway.up = [], True
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import backend.mycelium_bridge as mb
    monkeypatch.setattr(mb, "MYCELIUM_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("SIGNAL_LEDGER_ENABLED", "1")
    async def quote(symbol, mint):
        return (1.0, "mint")
    monkeypatch.setattr(sl, "_entry_price", quote)
    yield _Gateway
    srv.shutdown()


async def _clear_unmirrored():
    """Rows other tests left unmirrored would otherwise be mirrored here too."""
    from backend.db import get_db
    async with get_db() as db:
        await sl.ensure_tables(db)
        await db.execute("UPDATE signal_ledger SET mirrored_at=0 WHERE mirrored_at IS NULL")
        await db.commit()


async def _ingest(src, n=3, evidence=None):
    """Rows through the ledger's own write path (P1-03), as either door does."""
    for i in range(n):
        rid = f"01M{random.randint(0, 10**20):023d}"[:26]
        rec = {"id": rid, "schema": "signal_record/1", "asset": {"symbol": f"M{i}", "chain": "solana",
               "address": MINT}, "evidence": evidence or {"k": i}, "direction": "long"}
        assert await sl.write(sl.row_for("intel", {"symbol": f"M{i}", "source": src, "type": "alpha",
                                                   "conviction": 0.5, "mint": MINT}, None, rec))


async def _unmirrored(src):
    from backend.db import get_db
    async with get_db() as db:
        return (await (await db.execute(
            "SELECT COUNT(*) FROM signal_ledger WHERE source=? AND mirrored_at IS NULL", (src,))).fetchone())[0]


async def test_rows_appear_as_vantage_ledger_traces(client, gateway):
    await _clear_unmirrored()
    src = f"mirror_{random.randint(0, 10**9)}"
    await _ingest(src)
    out = await sl.mirror_once()
    assert out["posted"] == 3 and out["pending"] == 0
    assert await _unmirrored(src) == 0
    traces = [t for t in gateway.traces if t["agent"] == "vantage_ledger"]
    assert len(traces) == 3 and {t["action"] for t in traces} == {"signal_record"}
    assert all(t["kind"] == "observation" and t["payload"]["id"] == t["target"] for t in traces)
    assert traces[0]["payload"]["address"] == MINT  # the public mint survives for the resolver


async def test_outage_leaves_rows_unmirrored_then_catches_up(client, gateway):
    await _clear_unmirrored()
    src = f"mirror_out_{random.randint(0, 10**9)}"
    await _ingest(src, n=4)
    gateway.up = False
    out = await sl.mirror_once()
    assert out["posted"] == 0 and await _unmirrored(src) == 4
    gateway.up = True
    out = await sl.mirror_once()
    assert out["posted"] == 4 and await _unmirrored(src) == 0


async def test_a_lost_ack_never_doubles_a_trace(client, gateway, monkeypatch):
    await _clear_unmirrored()
    src = f"mirror_ack_{random.randint(0, 10**9)}"
    await _ingest(src, n=2)
    real = sl._post
    def posted_but_ack_lost(row):
        real(row)
        return False
    monkeypatch.setattr(sl, "_post", posted_but_ack_lost)
    await sl.mirror_once()  # first row lands at the gateway but is not marked
    monkeypatch.setattr(sl, "_post", real)
    out = await sl.mirror_once()
    assert out["skipped"] == 1 and out["posted"] == 1
    targets = [t["target"] for t in gateway.traces]
    assert len(targets) == len(set(targets)) == 2


async def test_evidence_wallets_are_scrubbed(client, gateway):
    await _clear_unmirrored()
    src = f"mirror_priv_{random.randint(0, 10**9)}"
    await _ingest(src, n=1, evidence={"top_buyer": WALLET, "buyers": 3})
    await sl.mirror_once()
    payload = gateway.traces[-1]["payload"]
    assert payload["evidence"]["buyers"] == 3 and payload["evidence"]["top_buyer"] != WALLET


async def test_flag_off_means_the_loop_never_mirrors(client, gateway, monkeypatch):
    monkeypatch.setenv("LEDGER_MIRROR_ENABLED", "0")
    monkeypatch.setattr(sl, "MIRROR_INTERVAL_S", 0)
    called = []
    async def spy(*a, **k):
        called.append(1)
        return {"posted": 0, "skipped": 0, "pending": 0}
    monkeypatch.setattr(sl, "mirror_once", spy)
    import asyncio
    task = asyncio.create_task(sl.mirror_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    assert called == []
