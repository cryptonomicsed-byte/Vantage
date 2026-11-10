"""Shared Helius pool: round-robin, cooldown skip, report, flag-off = old behaviour."""
import json
import time

import pytest

from backend import helius_pool as hp

POOL_SRC = '''
import json, time
CONFIG_PATH = {cfg!r}
STATE_PATH = {st!r}
def _config():
    return json.load(open(CONFIG_PATH))
def _state():
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {{}}
def report_error(provider, key, status_code, body=""):
    s = _state()
    s[provider + ":" + key] = time.time() + (3600 if "max usage" in body else 120)
    json.dump(s, open(STATE_PATH, "w"))
'''


@pytest.fixture
def pool(tmp_path, monkeypatch):
    cfg, st = tmp_path / "pools.json", tmp_path / "state.json"
    cfg.write_text(json.dumps({"helius": {"keys": ["k-aaaa1111", "k-bbbb2222", "k-cccc3333"], "assignments": {}}}))
    (tmp_path / "api_key_pool.py").write_text(POOL_SRC.format(cfg=str(cfg), st=str(st)))
    monkeypatch.setattr(hp, "ARES_HOME", str(tmp_path))
    monkeypatch.setattr(hp, "_mod", None)
    monkeypatch.setenv("HELIUS_POOL_ENABLED", "1")
    monkeypatch.setenv("HELIUS_API_KEY", "env-key")
    return st


def test_flag_off_is_the_single_env_key(pool, monkeypatch):
    monkeypatch.setenv("HELIUS_POOL_ENABLED", "0")
    assert {hp.get_key() for _ in range(5)} == {"env-key"}


def test_round_robin_uses_every_key(pool):
    assert {hp.get_key() for _ in range(6)} == {"k-aaaa1111", "k-bbbb2222", "k-cccc3333"}


def test_capped_key_is_skipped_and_shared(pool):
    hp.report("k-bbbb2222", 429, "max usage reached")
    assert "k-bbbb2222" not in {hp.get_key() for _ in range(6)}
    st = json.loads(pool.read_text())
    assert st["helius:k-bbbb2222"] > time.time() + 1000          # hard-quota cooldown, in the shared file


def test_all_capped_returns_empty_not_a_dead_key(pool):
    for k in hp.keys():
        hp.report(k, 429, "max usage reached")
    assert hp.get_key() == ""


def test_non_quota_errors_are_not_reported(pool):
    hp.report("k-aaaa1111", 500, "internal")
    assert not pool.exists()


def test_status_is_masked(pool):
    s = hp.status()
    assert s["keys"] == 3 and s["available"] == 3
    assert all(r["key"].startswith("…") and len(r["key"]) == 5 for r in s["per_key"])
    assert "k-aaaa1111" not in json.dumps(s)


def test_early_buyer_falls_back_to_public_rpc_when_all_capped(pool):
    from backend import early_buyer as eb
    for k in hp.keys():
        hp.report(k, 429, "max usage reached")
    assert eb._helius_key() == eb.PUBLIC_RPC


def test_gate_url_wins_when_set(pool, monkeypatch):
    from backend import early_buyer as eb
    monkeypatch.setenv("HELIUS_GATE_URL", "http://127.0.0.1:8092")
    assert eb._helius_key() == "http://127.0.0.1:8092"
    monkeypatch.setenv("HELIUS_GATE_URL", "")
    assert eb._helius_key() in hp.keys()
