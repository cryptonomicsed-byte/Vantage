"""P1-02: the SignalGenome proto is vendored, so validate-signal and the
pipeline registry work with TradingOS absent from disk, and the
validate-signal response shape is unchanged."""
import os

from backend import pipeline


def test_vendored_proto_is_the_signal_genome():
    path = pipeline.signal_proto_path()
    assert path.endswith(os.path.join("protocols", "tradingos", "signal.proto"))
    text = open(path).read()
    assert "message SignalGenome" in text


def test_fallback_is_the_tradingos_checkout(monkeypatch):
    monkeypatch.setattr(pipeline, "_VENDORED_SIGNAL_PROTO", "/nonexistent/signal.proto")
    assert pipeline.signal_proto_path() == "/opt/ares/TradingOS/protos/signal.proto"


def test_registry_probe_points_at_the_vendored_copy(monkeypatch):
    node = next(n for n in pipeline.NODES if n["name"] == "TradingOS")
    assert node["probe"]["path"] == pipeline._VENDORED_SIGNAL_PROTO
    ok, _ = pipeline._path_exists(node["probe"]["path"])
    assert ok


async def test_validate_signal_works_and_keeps_its_shape(client, fresh_agent):
    agent = await fresh_agent()
    r = await client.post("/api/pipeline/validate-signal", headers={"X-Agent-Key": agent["api_key"]},
                          json={"symbol": "SOL", "direction": "long", "conviction": 0.5, "source": "unit"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"conforms", "problems", "warnings", "schema", "proto"}
    assert body["schema"] == "tradingos.v1.SignalGenome"
    assert body["proto"] == pipeline._VENDORED_SIGNAL_PROTO and os.path.isfile(body["proto"])
