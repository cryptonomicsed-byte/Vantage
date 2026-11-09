"""P1-09: the Portent bridge reads the relay's real shapes and pushes once.

Acceptance: against a relay seeded with two predictors and one resolution,
the bridge pushes one resolution, one open-market skew and one leaderboard
row; a second cycle pushes nothing new. The fake relay answers exactly as
relay/portent_relay.py does at af8c426 (dict envelopes, `outcome`, per-
predictor `side`/`stake`, `composite`).
"""
import http.server
import importlib.util
import json
import pathlib
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = int(time.time())

RELAY = {
    "/stats": {"markets": 2},
    "/resolutions": {"resolutions": [
        {"market_id": "mkt-resolved-1", "oracle": "ab" * 32, "outcome": "yes",
         "confidence": 0.9, "resolved_at": NOW - 60, "cross_checks": 2}]},
    "/predictions": {"predictions": [
        {"market_id": "mkt-open-2", "predictor": "p1", "side": "yes", "stake": 1000, "created_at": NOW - 30},
        {"market_id": "mkt-open-2", "predictor": "p2", "side": "no", "stake": 200, "created_at": NOW - 20}]},
    "/leaderboard": {"leaderboard": [
        {"pubkey": "cd" * 32, "composite": 40, "tier": "verified", "resolved": 12,
         "hit_rate_bp": 6500, "updated_at": NOW - 10}]},
}


class _Relay(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = RELAY.get(self.path.split("?")[0])
        raw = json.dumps(body if body is not None else {"error": "unknown"}).encode()
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Relay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    spec = importlib.util.spec_from_file_location("portent_bridge", ROOT / "daemons" / "portent_signal_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "PORTENT_RELAY", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(mod, "_STATE_FILE", tmp_path / "state.json")
    sent = []
    monkeypatch.setattr(mod, "_vantage_ingest", lambda sig: sent.append(sig) or True)
    yield mod, sent
    srv.shutdown()


def test_one_of_each_then_nothing_new(bridge):
    mod, sent = bridge
    state = mod._one_cycle(mod._load_state())
    kinds = sorted(s["type"] for s in sent)
    assert kinds == ["momentum", "reputation_pulse", "resolution"], sent

    res = next(s for s in sent if s["type"] == "resolution")
    assert res["direction"] == "long" and res["conviction"] == 0.69  # 0.9 capped
    assert res["source_ref"] == "portent:mkt-resolved-1" and res["asset"]["chain"] == "portent"
    assert res["created_at"] == NOW - 60 and res["schema"] == "signal_record/1"

    mom = next(s for s in sent if s["type"] == "momentum")
    assert mom["evidence"]["yes_stake"] == 1000 and mom["evidence"]["no_stake"] == 200
    assert mom["direction"] == "long" and mom["conviction"] == round(800 / 1200, 4)

    rep = next(s for s in sent if s["type"] == "reputation_pulse")
    assert rep["direction"] == "alert" and rep["conviction"] == 0.4
    for s in sent:
        assert len(s["symbol"]) <= 20 and s["symbol"] == s["asset"]["symbol"]

    mod._save_state(state)
    sent.clear()
    mod._one_cycle(mod._load_state())
    assert sent == []


def test_a_changed_skew_is_pushed_again(bridge):
    mod, sent = bridge
    state = mod._one_cycle(mod._load_state())
    sent.clear()
    RELAY["/predictions"]["predictions"][1]["stake"] = 900  # 1000 vs 900: skew drops below 0.3
    RELAY["/predictions"]["predictions"].append(
        {"market_id": "mkt-open-2", "predictor": "p3", "side": "no", "stake": 3000, "created_at": NOW})
    try:
        mod._one_cycle(state)
        assert [s["direction"] for s in sent if s["type"] == "momentum"] == ["short"]
    finally:
        RELAY["/predictions"]["predictions"] = RELAY["/predictions"]["predictions"][:2]
        RELAY["/predictions"]["predictions"][1]["stake"] = 200


def test_relay_down_is_fail_open(bridge, monkeypatch):
    mod, sent = bridge
    monkeypatch.setattr(mod, "PORTENT_RELAY", "http://127.0.0.1:9")
    assert mod._one_cycle({}) == {} and sent == []
