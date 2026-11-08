"""signal_record/1 at the intel ingest door (handoff P1-01).

Acceptance from the ticket, each as a test:
  * a legacy payload returns today's exact response
  * a valid record is ingested and its id is idempotent
  * a record with no source_ref returns 422 and a reject row
  * conviction 1.2 still returns 422
  * a new source at 0.9 is stored at 0.69
plus the flag-off contract and the on-chain rule for new sources.
"""
import json
import os
import random
import time

import pytest

from backend import signal_record as sr

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MINT = "So111111111111" + "11111111111111111111111111111112"  # secret-scan:allow (wrapped SOL mint, public constant)


def _ulid() -> str:
    return "".join(random.choice(_CROCKFORD) for _ in range(26))


def _headers():
    return {"X-Vantage-Tool": "intel", "X-Vantage-Tool-Key": os.environ["VANTAGE_TOOL_INTEL"]}


def _record(**over):
    r = {
        "schema": "signal_record/1",
        "id": _ulid(),
        "created_at": int(time.time()) - 5,
        "source": over.pop("source", f"newsrc{random.randint(0, 10**9)}"),
        "source_ref": "tx:5h3kQ1",
        "asset": {"symbol": "WSOL", "chain": "solana", "address": MINT},
        "type": "convergence",
        "direction": "long",
        "conviction": 0.62,
        "producer": {"kind": "daemon", "agent_id": None, "version": "t 0.1",
                     "lineage": "unit", "env": "ci", "blind": True},
        "evidence": {"buyers": 7},
        "tags": ["pumpfun"],
        "detail": "unit",
    }
    r.update(over)
    return r


@pytest.fixture
def v1_on(monkeypatch):
    monkeypatch.setenv("SIGNAL_RECORD_V1", "1")
    calls = []

    async def exists(chain, address):
        calls.append((chain, address))
        return True
    monkeypatch.setattr(sr, "address_checker", exists)
    sr._verified.clear()
    return calls


async def _pool_row(symbol, source):
    from backend.db import get_db
    async with get_db() as db:
        return await (await db.execute(
            "SELECT conviction, direction, mint FROM signal_pool WHERE symbol=? AND source=? "
            "ORDER BY id DESC LIMIT 1", (symbol, source))).fetchone()


async def _rejects_for(source):
    from backend.db import get_db
    async with get_db() as db:
        return await (await db.execute(
            "SELECT reasons, payload FROM signal_rejects WHERE source=?", (source,))).fetchall()


async def test_legacy_payload_response_is_unchanged_with_flag_on(client, v1_on, monkeypatch):
    body = {"symbol": "LEGACY1", "source": "unit_legacy", "type": "alpha", "conviction": 0.9,
            "direction": "BUY"}
    on = (await client.post("/api/intel/signals/ingest", json=body, headers=_headers())).json()
    monkeypatch.setenv("SIGNAL_RECORD_V1", "0")
    off = (await client.post("/api/intel/signals/ingest", json=body, headers=_headers())).json()
    assert set(on) == set(off) == {"status", "signal", "pool_size"}
    assert on["status"] == off["status"] == "ingested" and on["signal"] == "LEGACY1"
    # legacy conviction is never capped, even for a source the snapshot never saw
    assert (await _pool_row("LEGACY1", "unit_legacy"))[0] == 0.9


async def test_legacy_missing_field_still_returns_200_error(client, v1_on):
    r = await client.post("/api/intel/signals/ingest", json={"symbol": "X", "source": "s"},
                          headers=_headers())
    assert r.status_code == 200 and r.json() == {"error": "Missing required field: type"}


async def test_flag_off_treats_a_record_as_legacy(client, monkeypatch):
    monkeypatch.setenv("SIGNAL_RECORD_V1", "0")
    rec = _record(symbol="OFFREC", source="unit_off")  # legacy fields alongside
    rec["type"] = "alpha"
    r = await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())
    assert r.status_code == 200
    assert set(r.json()) == {"status", "signal", "pool_size"}  # no record keys at all


async def test_valid_record_is_ingested_and_id_is_idempotent(client, v1_on):
    rec = _record()
    r1 = await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())
    assert r1.status_code == 200, r1.text
    b = r1.json()
    assert b["status"] == "ingested" and b["id"] == rec["id"] and b["schema"] == "signal_record/1"
    row = await _pool_row("WSOL", rec["source"])
    assert row is not None and row[1] == "LONG" and row[2] == MINT

    r2 = await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"
    from backend.db import get_db
    async with get_db() as db:
        n = (await (await db.execute(
            "SELECT COUNT(*) FROM signal_pool WHERE source=?", (rec["source"],))).fetchone())[0]
    assert n == 1


async def test_missing_source_ref_is_422_with_a_reject_row(client, v1_on):
    rec = _record()
    del rec["source_ref"]
    r = await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())
    assert r.status_code == 422
    assert any("source_ref" in p for p in r.json()["problems"])
    rows = await _rejects_for(rec["source"])
    assert len(rows) == 1 and "source_ref" in rows[0][0]
    assert json.loads(rows[0][1])["id"] == rec["id"]


async def test_conviction_above_one_is_still_422(client, v1_on):
    rec = _record(conviction=1.2)
    r = await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())
    assert r.status_code == 422 and any("conviction" in p for p in r.json()["problems"])
    # and the legacy door still refuses it too
    r = await client.post("/api/intel/signals/ingest", headers=_headers(),
                          json={"symbol": "C", "source": "s", "type": "t", "conviction": 1.2})
    assert r.status_code == 422


async def test_new_source_at_0_9_is_stored_at_0_69(client, v1_on):
    rec = _record(conviction=0.9)
    b = (await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())).json()
    assert b["conviction"] == 0.69 and b["conviction_raw"] == 0.9 and b["capped"] is True
    assert (await _pool_row("WSOL", rec["source"]))[0] == 0.69


async def test_established_source_is_not_capped(client, v1_on):
    from backend.db import get_db
    src = "unit_established"
    async with get_db() as db:
        await sr.ensure_tables(db)
        await db.execute("INSERT OR REPLACE INTO source_caps VALUES (?,?,?,?)",
                         (src, 1.0, "legacy-snapshot", "2026-11-08 00:00:00"))
        await db.commit()
    b = (await client.post("/api/intel/signals/ingest", json=_record(source=src, conviction=0.9),
                           headers=_headers())).json()
    assert b["conviction"] == 0.9 and b["capped"] is False


async def test_new_source_needs_a_verified_address(client, v1_on, monkeypatch):
    async def unknown(chain, address):
        return None
    monkeypatch.setattr(sr, "address_checker", unknown)
    rec = _record()
    r = await client.post("/api/intel/signals/ingest", json=rec, headers=_headers())
    assert r.status_code == 422 and any("could not be verified" in p for p in r.json()["problems"])

    async def missing(chain, address):
        return False
    monkeypatch.setattr(sr, "address_checker", missing)
    r = await client.post("/api/intel/signals/ingest", json=_record(), headers=_headers())
    assert r.status_code == 422 and any("does not exist" in p for p in r.json()["problems"])


async def test_snapshot_freezes_existing_sources_at_cap_one(client, v1_on):
    from backend.db import get_db
    async with get_db() as db:
        await sr.ensure_tables(db)
        mark = await (await db.execute(
            "SELECT 1 FROM source_caps WHERE source=?", (sr.LEGACY_SNAPSHOT_MARK,))).fetchone()
        legacy = await (await db.execute(
            "SELECT COUNT(*) FROM source_caps WHERE set_by='legacy-snapshot' AND cap=1.0")).fetchone()
    assert mark is not None and legacy[0] >= 1


# ── pure validator ──────────────────────────────────────────────────────────

def test_validator_maps_legacy_directions_and_catches_unit_bugs():
    now = int(time.time())
    rec, p = sr.validate(_record(direction="BULLISH"), now=now)
    assert not p and rec["direction"] == "long"
    rec, p = sr.validate(_record(direction="sell"), now=now)
    assert not p and rec["direction"] == "short"
    _, p = sr.validate(_record(created_at=now * 1000), now=now)
    assert any("future" in x for x in p)
    _, p = sr.validate(_record(asset={"symbol": "X", "chain": "solana", "address": "0xabc"}), now=now)
    assert any("Solana" in x for x in p)
    _, p = sr.validate(_record(asset={"symbol": "X", "chain": "polygon", "address": ""}), now=now)
    assert any("asset.chain" in x for x in p)
    _, p = sr.validate(_record(evidence={"blob": "x" * 9000}), now=now)
    assert any("8 KB" in x for x in p)
    _, p = sr.validate(_record(id="not-a-ulid"), now=now)
    assert any("ULID" in x for x in p)
