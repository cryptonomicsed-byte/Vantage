"""Opt-in intel exchange between sovereign instances.

The centre of gravity here is not "does sharing work" — it's that a peer can
never reach this instance's execution path. trading.py auto-creates a real
order above conviction 0.7, so a careless import feature would hand every
peer a remote trading trigger. Those are the tests that matter.
"""
import secrets
import time

import pytest
import pytest_asyncio
from coincurve import PrivateKey

from backend import intel_exchange as exchange
from backend.buzz_client import build_event
from backend.buzz_identity import public_key_xonly_hex
from backend.config import settings


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await exchange.init_intel_exchange_db()
    from backend.routers.intel import _ensure_signal_tables
    from backend.db import get_db

    async with get_db() as db:
        await _ensure_signal_tables(db)
        await db.commit()


@pytest_asyncio.fixture
async def peer():
    """A federation peer with a pinned pubkey — the TOFU trust anchor the
    export endpoint authenticates against."""
    from backend.db import get_db

    key = PrivateKey()
    pubkey = public_key_xonly_hex(key)
    url = f"https://peer-{secrets.token_hex(4)}.example"
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO federation_peers (url, name, status, nostr_pubkey) VALUES (?,?,'active',?)",
            (url, f"Peer{secrets.token_hex(2)}", pubkey),
        )
        peer_id = cur.lastrowid
        await db.commit()
    return {"id": peer_id, "url": url, "key": key, "pubkey": pubkey}


@pytest_asyncio.fixture
def admin():
    return {"X-Admin-Key": settings.ADMIN_KEY}


async def _add_local_signal(symbol="SOL", source="birdeye", type_="momentum", conviction=0.9):
    from backend.db import get_db

    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO signal_pool (symbol, source, type, conviction, direction, detail, mint, ts)
               VALUES (?,?,?,?,'long','local alpha','',?)""",
            (symbol, source, type_, conviction, int(time.time())),
        )
        await db.commit()
        return cur.lastrowid


# ── the safety property ──────────────────────────────────────────────────────

def test_imported_conviction_is_clamped_below_auto_execution():
    """The whole feature rests on this. trading.py auto-creates a real order
    above 0.7, so a peer's number must never be able to reach it."""
    assert exchange.IMPORT_CONVICTION_CEILING < exchange.AUTO_EXECUTION_THRESHOLD

    for hostile in (0.99, 1.0, 5.0, 7.0, 1e9, float("inf")):
        assert exchange.clamp_imported_conviction(hostile) < exchange.AUTO_EXECUTION_THRESHOLD


def test_clamping_survives_garbage():
    assert exchange.clamp_imported_conviction(float("nan")) == 0.0
    assert exchange.clamp_imported_conviction(-5) == 0.0
    assert exchange.clamp_imported_conviction("not a number") == 0.0
    assert exchange.clamp_imported_conviction(None) == 0.0


def test_no_trust_tier_executes():
    """There is deliberately no 'execution' tier. A peer can inform your
    decisions; it cannot make them."""
    assert exchange.TRUST_TIERS == {"advisory", "pooled"}
    assert "execution" not in exchange.TRUST_TIERS


@pytest.mark.asyncio
async def test_imported_signals_never_enter_the_local_pool(client, peer):
    """Quarantine is structural: imported rows land in their own table."""
    from backend.db import get_db

    await exchange.set_agreement(peer_id=peer["id"], direction="import")
    await exchange.import_signals(peer["id"], [{
        "remote_id": "r1", "symbol": "BONK", "source": "peer-src",
        "type": "momentum", "conviction": 0.95, "ts": int(time.time()),
    }])

    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM signal_pool WHERE symbol='BONK'")
        in_pool = (await cur.fetchone())[0]
        cur = await db.execute("SELECT conviction FROM imported_signals WHERE symbol='BONK'")
        imported = await cur.fetchone()

    assert in_pool == 0, "an imported signal must not reach the local pool on its own"
    assert imported[0] < exchange.AUTO_EXECUTION_THRESHOLD


@pytest.mark.asyncio
async def test_even_a_promoted_signal_stays_below_the_threshold(client, peer):
    await exchange.set_agreement(peer_id=peer["id"], direction="import")
    await exchange.import_signals(peer["id"], [{
        "remote_id": "r2", "symbol": "WIF", "source": "peer-src",
        "type": "momentum", "conviction": 1.0, "ts": int(time.time()),
    }])
    row = (await exchange.list_imported(peer_id=peer["id"]))[0]

    result = await exchange.promote_imported(row["id"])
    assert result["promoted"] is True
    assert result["conviction"] < exchange.AUTO_EXECUTION_THRESHOLD
    assert result["source"].startswith("peer:")


@pytest.mark.asyncio
async def test_a_promoted_signal_is_attributed_to_its_peer(client, peer):
    """Nothing downstream should mistake a peer's opinion for local work."""
    from backend.db import get_db

    await exchange.set_agreement(peer_id=peer["id"], direction="import")
    await exchange.import_signals(peer["id"], [{
        "remote_id": "r3", "symbol": "JUP", "source": "peer-src",
        "type": "momentum", "conviction": 0.6, "ts": int(time.time()),
    }])
    row = (await exchange.list_imported(peer_id=peer["id"]))[0]
    await exchange.promote_imported(row["id"])

    async with get_db() as db:
        cur = await db.execute("SELECT source, detail FROM signal_pool WHERE symbol='JUP'")
        pooled = await cur.fetchone()
    assert pooled[0].startswith("peer:")
    assert "via" in pooled[1]


# ── consent: both halves required ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nothing_is_shared_without_an_export_agreement(client, peer):
    await _add_local_signal()
    with pytest.raises(exchange.ExchangeRefused):
        await exchange.signals_for_peer(peer["id"])


@pytest.mark.asyncio
async def test_nothing_is_accepted_without_an_import_agreement(client, peer):
    with pytest.raises(exchange.ExchangeRefused):
        await exchange.import_signals(peer["id"], [{"remote_id": "x", "symbol": "SOL"}])


@pytest.mark.asyncio
async def test_revoking_stops_the_flow_immediately(client, peer):
    await _add_local_signal()
    await exchange.set_agreement(peer_id=peer["id"], direction="export")
    assert await exchange.signals_for_peer(peer["id"]) != []

    await exchange.set_agreement(peer_id=peer["id"], direction="export", status="revoked")
    with pytest.raises(exchange.ExchangeRefused):
        await exchange.signals_for_peer(peer["id"])


# ── granularity ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_is_filtered_by_source(client, peer):
    await _add_local_signal(symbol="AAA", source="birdeye")
    await _add_local_signal(symbol="BBB", source="secret-edge")

    await exchange.set_agreement(peer_id=peer["id"], direction="export", sources=["birdeye"])
    shared = await exchange.signals_for_peer(peer["id"])
    symbols = {s["symbol"] for s in shared}

    assert "AAA" in symbols
    assert "BBB" not in symbols, "a source outside the agreement must not leak"


@pytest.mark.asyncio
async def test_export_is_filtered_by_conviction_floor(client, peer):
    await _add_local_signal(symbol="LOW", conviction=0.2)
    await _add_local_signal(symbol="HIGH", conviction=0.9)

    await exchange.set_agreement(peer_id=peer["id"], direction="export", min_conviction=0.5)
    symbols = {s["symbol"] for s in await exchange.signals_for_peer(peer["id"])}
    assert "HIGH" in symbols
    assert "LOW" not in symbols


@pytest.mark.asyncio
async def test_a_peer_cannot_widen_its_own_entitlement(client, peer):
    """Filtering happens on the exporting side, so asking differently cannot
    get you more."""
    await _add_local_signal(symbol="SECRET", source="secret-edge")
    await exchange.set_agreement(peer_id=peer["id"], direction="export", sources=["birdeye"])

    shared = await exchange.signals_for_peer(peer["id"], limit=500, since=0)
    assert all(s["source"] == "birdeye" for s in shared)


@pytest.mark.asyncio
async def test_import_filters_what_the_agreement_excludes(client, peer):
    await exchange.set_agreement(
        peer_id=peer["id"], direction="import", sources=["trusted-src"], min_conviction=0.4
    )
    result = await exchange.import_signals(peer["id"], [
        {"remote_id": "a", "symbol": "X", "source": "trusted-src", "conviction": 0.5},
        {"remote_id": "b", "symbol": "Y", "source": "junk-src", "conviction": 0.9},
        {"remote_id": "c", "symbol": "Z", "source": "trusted-src", "conviction": 0.1},
    ])
    assert result["accepted"] == 1
    assert result["filtered"] == 2


@pytest.mark.asyncio
async def test_importing_is_idempotent_on_remote_id(client, peer):
    await exchange.set_agreement(peer_id=peer["id"], direction="import")
    batch = [{"remote_id": "dup", "symbol": "SOL", "source": "s", "conviction": 0.5}]

    first = await exchange.import_signals(peer["id"], batch)
    second = await exchange.import_signals(peer["id"], batch)
    assert first["accepted"] == 1
    assert second["accepted"] == 0


# ── the peer-facing export endpoint ──────────────────────────────────────────

async def _pull_as(client, peer, since=0):
    ch = await client.post("/api/intel/exchange/challenge", json={"pubkey": peer["pubkey"]})
    assert ch.status_code == 200, ch.text
    challenge = ch.json()["challenge"]
    signed = build_event(
        peer["key"], kind=22242, content="",
        tags=[["relay", "https://us.example"], ["challenge", challenge]],
    )
    return await client.post(
        "/api/intel/exchange/export",
        json={"signed_event": signed, "challenge": challenge, "since": since},
    )


@pytest.mark.asyncio
async def test_an_authenticated_peer_receives_its_entitlement(client, peer):
    await _add_local_signal(symbol="SHARED", source="birdeye")
    await exchange.set_agreement(peer_id=peer["id"], direction="export", sources=["birdeye"])

    resp = await _pull_as(client, peer)
    assert resp.status_code == 200, resp.text
    assert any(s["symbol"] == "SHARED" for s in resp.json()["signals"])


@pytest.mark.asyncio
async def test_an_unauthenticated_caller_gets_nothing(client, peer):
    await _add_local_signal()
    await exchange.set_agreement(peer_id=peer["id"], direction="export")

    resp = await client.post(
        "/api/intel/exchange/export",
        json={"signed_event": {"id": "x"}, "challenge": "nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_signing_with_the_wrong_key_is_refused(client, peer):
    await _add_local_signal()
    await exchange.set_agreement(peer_id=peer["id"], direction="export")

    ch = await client.post("/api/intel/exchange/challenge", json={"pubkey": peer["pubkey"]})
    challenge = ch.json()["challenge"]
    impostor = PrivateKey()
    signed = build_event(
        impostor, kind=22242, content="",
        tags=[["relay", "https://x.example"], ["challenge", challenge]],
    )
    resp = await client.post(
        "/api/intel/exchange/export",
        json={"signed_event": signed, "challenge": challenge},
    )
    assert resp.status_code == 401
    assert "different key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_challenge_cannot_be_replayed(client, peer):
    await _add_local_signal()
    await exchange.set_agreement(peer_id=peer["id"], direction="export")

    ch = await client.post("/api/intel/exchange/challenge", json={"pubkey": peer["pubkey"]})
    challenge = ch.json()["challenge"]
    signed = build_event(
        peer["key"], kind=22242, content="",
        tags=[["relay", "https://x.example"], ["challenge", challenge]],
    )
    body = {"signed_event": signed, "challenge": challenge}

    assert (await client.post("/api/intel/exchange/export", json=body)).status_code == 200
    assert (await client.post("/api/intel/exchange/export", json=body)).status_code == 401


@pytest.mark.asyncio
async def test_a_flagged_peer_is_cut_off(client, peer):
    """The operator's kill switch for a whole instance."""
    from backend.db import get_db

    await _add_local_signal()
    await exchange.set_agreement(peer_id=peer["id"], direction="export")
    async with get_db() as db:
        await db.execute("UPDATE federation_peers SET flagged=1 WHERE id=?", (peer["id"],))
        await db.commit()

    resp = await _pull_as(client, peer)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_identity_cannot_probe_for_agreements(client):
    """Unknown and flagged must be indistinguishable, or the endpoint becomes
    a directory of who you share with."""
    stranger = PrivateKey()
    pubkey = public_key_xonly_hex(stranger)
    ch = await client.post("/api/intel/exchange/challenge", json={"pubkey": pubkey})
    challenge = ch.json()["challenge"]
    signed = build_event(
        stranger, kind=22242, content="",
        tags=[["relay", "https://x.example"], ["challenge", challenge]],
    )
    resp = await client.post(
        "/api/intel/exchange/export",
        json={"signed_event": signed, "challenge": challenge},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "no intel agreement for this identity"


# ── operator surface ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_configuring_agreements_requires_the_admin_key(client, peer):
    resp = await client.post(
        "/api/intel/exchange/agreements",
        data={"peer_id": peer["id"], "direction": "export"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_the_agreements_view_states_the_execution_policy(client, peer, admin):
    resp = await client.get("/api/intel/exchange/agreements", headers=admin)
    assert resp.status_code == 200, resp.text
    policy = resp.json()["policy"]
    assert policy["import_conviction_ceiling"] < policy["auto_execution_threshold"]
    assert "explicit act" in policy["note"]


@pytest.mark.asyncio
async def test_an_export_agreement_reminds_you_the_peer_must_opt_in_too(client, peer, admin):
    resp = await client.post(
        "/api/intel/exchange/agreements",
        data={"peer_id": peer["id"], "direction": "export", "sources": '["birdeye"]'},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    assert "import agreement" in resp.json()["reminder"]


@pytest.mark.asyncio
async def test_an_unknown_peer_is_refused(client, admin):
    resp = await client.post(
        "/api/intel/exchange/agreements",
        data={"peer_id": 999999, "direction": "export"}, headers=admin,
    )
    assert resp.status_code == 422
    assert "no such federation peer" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_an_invalid_trust_tier_is_refused(client, peer, admin):
    resp = await client.post(
        "/api/intel/exchange/agreements",
        data={"peer_id": peer["id"], "direction": "import", "trust_tier": "execution"},
        headers=admin,
    )
    assert resp.status_code == 422
