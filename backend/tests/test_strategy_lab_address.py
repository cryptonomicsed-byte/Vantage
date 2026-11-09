"""P1-07: strategy-lab base address from env, probe /health.

Acceptance: pipeline status shows strategy-lab up; enrich_from_strategy_lab
returns a score for a known token. Exercised against a local stub hub that
behaves like hub/score.py (404 on '/', 200 on /health, a score from
/api/convergence), so the test does not depend on the VPS.
"""
import http.server
import importlib
import json
import threading

import pytest


class _Hub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/health"):
            body, code = {"ok": True}, 200
        elif self.path.startswith("/api/convergence"):
            body, code = {"score": 72.5, "verdict": "LEAN"}, 200
        else:
            body, code = {"error": "not found"}, 404
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def hub(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("STRATEGY_LAB_BASE", base)
    yield base
    srv.shutdown()
    # Leave the module globals as production would see them for later tests.
    monkeypatch.delenv("STRATEGY_LAB_BASE", raising=False)
    from backend import pipeline, signal_fusion
    importlib.reload(signal_fusion)
    importlib.reload(pipeline)


def test_default_base_is_unchanged(monkeypatch):
    monkeypatch.delenv("STRATEGY_LAB_BASE", raising=False)
    from backend import signal_fusion
    importlib.reload(signal_fusion)
    assert signal_fusion.STRATEGY_LAB_BASE == "http://localhost:8012"


async def test_enrich_reads_the_env_base_and_returns_a_score(hub):
    from backend import signal_fusion
    importlib.reload(signal_fusion)
    assert signal_fusion.STRATEGY_LAB_BASE == hub
    out = await signal_fusion.enrich_from_strategy_lab({"symbol": "X", "mint": "Tok111"})
    assert out["strategy_lab_score"] == 72.5


def test_probe_is_health_and_reads_up(hub):
    from backend import pipeline
    importlib.reload(pipeline)
    node = next(n for n in pipeline.NODES if n["probe"].get("url", "").endswith("/health")
                and n["probe"]["url"].startswith(hub))
    ok, detail = pipeline._http_alive(node["probe"]["url"])
    assert ok and detail == "HTTP 200"
