"""P3-01 / P3-03: Crucible trading adapter + daily calibration events.

Unit tests run everywhere. The end-to-end tests drive the REAL crucible CLI
(claim.build / probe.run / oracle.verdict / event.verify) when a built binary
and the signal-outcome falsifier are present, and are skipped otherwise.
All keys are throwaway keys generated in tmp_path.
"""
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import pytest

from backend import crucible_adapter as ca
from backend import crucible_calibration as cc
from backend import crucible_events as ce
from backend.db import get_db

import shutil

# The end-to-end tests need a built crucible CLI and Crucible's
# examples/falsifiers (with signal-outcome.wat): point CRUCIBLE_TEST_BIN and
# CRUCIBLE_TEST_FALSIFIERS at them. Without both they skip -- never error.
BIN = Path(os.environ.get("CRUCIBLE_TEST_BIN") or shutil.which("crucible") or "/nonexistent/crucible")
FALS = Path(os.environ.get("CRUCIBLE_TEST_FALSIFIERS") or "/nonexistent/falsifiers")


def _have_cli() -> bool:
    try:
        return BIN.is_file() and os.access(BIN, os.X_OK) and (FALS / "signal-outcome.wat").is_file()
    except OSError:  # e.g. a path under a directory the runner can't read
        return False


HAVE_CLI = _have_cli()
needs_cli = pytest.mark.skipif(not HAVE_CLI, reason="set CRUCIBLE_TEST_BIN + CRUCIBLE_TEST_FALSIFIERS to run")


def _key(path: Path) -> bytes:
    sk = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sk.hex())
    path.chmod(0o600)
    return sk


@pytest.fixture
def keys(tmp_path, monkeypatch):
    kd = tmp_path / "sources"
    k = {"alpha": _key(kd / "alpha.key"), "beta": _key(kd / "beta.key"),
         "router": _key(tmp_path / "router.key"), "resolver": _key(tmp_path / "resolver.key")}
    monkeypatch.setenv("CRUCIBLE_SOURCE_KEYS_DIR", str(kd))
    monkeypatch.setenv("CRUCIBLE_ROUTER_KEY_FILE", str(tmp_path / "router.key"))
    monkeypatch.setenv("CRUCIBLE_RESOLVER_KEY_FILE", str(tmp_path / "resolver.key"))
    return k


# ── unit ─────────────────────────────────────────────────────────────────────

def test_match_key_by_address_never_ticker():
    a = {"chain": "solana", "address": "MintA", "symbol": "PEPE", "direction": "LONG"}
    b = {"chain": "solana", "address": "MintB", "symbol": "PEPE", "direction": "long"}
    assert ca.match_key(a) != ca.match_key(b)
    assert ca.match_key({"chain": "base", "symbol": "eth", "direction": "short"}) == "base|sym:ETH|short"


def test_signer_rules(keys):
    src, att = ca.signer_for({"source": "alpha", "producer": "{}"})
    assert src == keys["alpha"] and att is True
    g, att = ca.signer_for({"source": "x", "producer": json.dumps({"kind": "guild_agent", "agent_id": 3})})
    assert g == keys["router"] and att is False
    assert ca.signer_for({"source": "nokey", "producer": "{}"})[0] is None


def test_world_readable_key_refused(tmp_path, monkeypatch):
    p = tmp_path / "s" / "loose.key"
    p.parent.mkdir()
    p.write_text(secrets.token_bytes(32).hex())
    p.chmod(0o644)
    monkeypatch.setenv("CRUCIBLE_SOURCE_KEYS_DIR", str(p.parent))
    assert ce.source_key("loose") is None


def test_confidence_open_interval_and_outcome_mapping():
    assert ca.confidence({"conviction": 1.0}) == 0.99 and ca.confidence({"conviction": 0}) == 0.01
    assert ca.outcome_of({"hit": True, "scored": True}) == "holds"
    assert ca.outcome_of({"hit": False, "scored": True}) == "fails"
    assert ca.outcome_of({"hit": None}) == "indeterminate"
    assert ca.outcome_of({"hit": True, "scored": False}) == "indeterminate"


def test_sign_verify_roundtrip_and_tamper():
    sk = secrets.token_bytes(32)
    ev = ce.sign({"created_at": 5, "kind": 47002, "tags": [["c", "x"]], "content": "hi"}, sk)
    assert ce.verify(ev)
    assert not ce.verify({**ev, "content": "changed"})
    with pytest.raises(ValueError):
        ce.sign({**ev, "pubkey": "00" * 32}, sk)


@pytest.mark.asyncio
async def test_off_and_without_cli_does_nothing(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_BIN", "/nonexistent/crucible")
    assert not ca.enabled()
    assert (await ca.process_once())["crucible"] == "unavailable"
    assert (await ca.verdict_once())["crucible"] == "unavailable"


# ── calibration (P3-03): in-process, no CLI needed ───────────────────────────

FINDINGS = [
    {"source": "alpha", "window": "24h", "n": 120, "hits": 70, "n_eff": 80, "hits_eff": 46.5,
     "hit_rate": 0.58, "brier": 0.231, "min_samples_met": True, "updated_at": "2026-11-09T00:00:00Z"},
    {"source": "fresh", "window": "24h", "n": 4, "hits": 3, "hit_rate": None, "brier": None,
     "min_samples_met": False},
]


@pytest.mark.asyncio
async def test_calibration_daily_signed_idempotent(keys):
    now = 1_794_200_000
    out = await cc.publish_day(now, fetch=lambda: FINDINGS)
    assert out == {"events": 2, "already": 0, "status": "ok"}
    assert (await cc.publish_day(now + 60, fetch=lambda: FINDINGS))["already"] == 2  # same day: none new
    async with get_db() as db:
        rows = [json.loads(r[0]) for r in await (await db.execute(
            "SELECT event FROM crucible_events WHERE kind=47005 AND record_id LIKE ?",
            (f"calibration:%:{cc.day_of(now)}",))).fetchall()]
    by = {json.loads(e["content"])["source"]: e for e in rows}
    assert set(by) >= {"alpha", "fresh"}
    resolver_pk = ce.pubkey_hex(keys["resolver"])
    for e in by.values():
        assert ce.verify(e) and e["pubkey"] == resolver_pk  # resolver signs, never the source
    tags = {t[0]: t[1] for t in by["alpha"]["tags"]}
    assert tags["d"] == f"alpha:24h:{cc.day_of(now)}" and tags["p"] == ce.pubkey_hex(keys["alpha"])
    assert "p" not in {t[0] for t in by["fresh"]["tags"]}  # no key on the box for 'fresh'
    assert json.loads(by["fresh"]["content"])["hit_rate"] is None  # below the floor stays null
    assert json.loads(by["alpha"]["content"])["n_eff"] == 80


@pytest.mark.asyncio
async def test_calibration_fail_open(keys, monkeypatch):
    assert (await cc.publish_day(fetch=lambda: None))["status"] == "mycelium_unreachable"
    monkeypatch.setenv("CRUCIBLE_RESOLVER_KEY_FILE", "/nonexistent")
    assert (await cc.publish_day(fetch=lambda: FINDINGS))["status"] == "no_resolver_key"


# ── end to end against the real crucible CLI ─────────────────────────────────

async def _ledger(rows):
    from backend.signal_ledger import ensure_tables
    async with get_db() as db:
        await ensure_tables(db)
        await ca.ensure_tables(db)
        for r in rows:
            await db.execute(
                "INSERT INTO signal_ledger (id, door, source, type, symbol, chain, address, direction, "
                "conviction, horizon_hours, entry_price_usd, producer, created_at, ingested_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], "intel", r["source"], "alpha", r.get("symbol", "TOK"), "solana", r.get("address"),
                 r["direction"], r.get("conviction", 0.6), 24, 1.25, r.get("producer", "{}"),
                 r["created_at"], r["created_at"]))
        await db.commit()


def _cli_verify(ev) -> bool:
    out = subprocess.run([str(BIN), "event.verify"], input=json.dumps({"events": [ev]}).encode(),
                         capture_output=True, timeout=20)
    return out.returncode == 0 and json.loads(out.stdout or b"{}").get("all_valid") is True


@needs_cli
@pytest.mark.asyncio
async def test_e2e_claim_attest_verdict_with_real_cli(keys, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_BIN", str(BIN))
    monkeypatch.setenv("CRUCIBLE_FALSIFIER_DIR", str(FALS))
    now = int(time.time())
    mint = "E2EMint" + secrets.token_hex(6)
    other = "E2EOther" + secrets.token_hex(6)
    tag = secrets.token_hex(4)
    # the live ledger stores what producers sent (BUY / buy / NEUTRAL / SELL)
    await _ledger([
        {"id": f"r1-{tag}", "source": "alpha", "address": mint, "direction": "BUY", "created_at": now - 7200},
        {"id": f"r2-{tag}", "source": "beta", "address": mint, "direction": "buy", "created_at": now - 6600},
        {"id": f"r3-{tag}", "source": "beta", "address": mint, "direction": "NEUTRAL", "created_at": now - 6500},
        {"id": f"r4-{tag}", "source": "nokey", "address": other, "direction": "long", "created_at": now - 6400},
        {"id": f"r5-{tag}", "source": "gx", "address": other, "direction": "SELL", "created_at": now - 6300,
         "producer": json.dumps({"kind": "guild_agent", "agent_id": 42})},
    ])
    st = await ca.process_once(now=now)
    assert st["crucible"] == "ok", st
    assert st["claims"] >= 2 and st["attestations"] >= 1 and st["skipped"] >= 1 and st["no_key"] >= 1, st

    async with get_db() as db:
        claims = {r[0]: r for r in await (await db.execute(
            "SELECT record_id, claim_id, attestations, attested, window FROM crucible_claims "
            "WHERE record_id LIKE ?", (f"%-{tag}",))).fetchall()}
        evs = [json.loads(r[0]) for r in await (await db.execute(
            "SELECT event FROM crucible_events WHERE record_id LIKE ?", (f"%-{tag}",))).fetchall()]
    assert set(claims) == {f"r1-{tag}", f"r5-{tag}"}          # r2 attested r1's claim; r3/r4 not claims
    assert claims[f"r1-{tag}"][2] == 1                          # beta confirmed alpha's call
    assert claims[f"r5-{tag}"][3] == 0                          # guild agent: attested false
    assert {e["kind"] for e in evs} == {47001, 47002}
    for e in evs:
        assert ce.verify(e) and _cli_verify(e)
    guild = next(e for e in evs if ["via", "guild-router"] in e["tags"])
    assert guild["pubkey"] == ce.pubkey_hex(keys["router"]) and ["attested", "false"] in guild["tags"]

    # the resolver's mark settles r1's claim once its window has passed
    later = now + 24 * 3600
    marks = {f"r1-{tag}": {"window": "24h", "hit": True, "scored": True, "marked_at": later - 60},
             f"r5-{tag}": {"window": "24h", "hit": None, "scored": False, "marked_at": later - 60}}
    vs = await ca.verdict_once(now=later, fetch=lambda since, w: marks)
    assert vs["verdicts"] >= 1 and vs["indeterminate"] >= 1, vs
    async with get_db() as db:
        v = json.loads((await (await db.execute(
            "SELECT event FROM crucible_events WHERE kind=47010 AND record_id=?", (f"r1-{tag}",))).fetchone())[0])
        oc = dict(await (await db.execute(
            "SELECT record_id, verdict_outcome FROM crucible_claims WHERE record_id LIKE ?",
            (f"%-{tag}",))).fetchall())
    assert v["pubkey"] == ce.pubkey_hex(keys["resolver"]) and ce.verify(v) and _cli_verify(v)
    assert oc == {f"r1-{tag}": "holds", f"r5-{tag}": "indeterminate"}
