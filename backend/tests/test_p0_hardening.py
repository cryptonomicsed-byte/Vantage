"""P0 hardening: Sentinel daemon control, fail-closed secrets, locked
federation, hashed guild keys, retired host-side repo writes."""
import hashlib
import secrets

import pytest
import pytest_asyncio

from backend import esu_gate
from backend.config import settings
from backend.db import get_db

from .sentinel_helpers import ADMIN, operator

pytestmark = pytest.mark.asyncio
UNIT = "ares-test-unit.service"


@pytest_asyncio.fixture
async def sysd(monkeypatch):
    from backend.routers import daemons as d
    calls = []

    async def fake(*args, timeout=15.0):
        calls.append(args)
        if args[0] == "list-units":
            return 0, f"{UNIT} loaded active running Test unit\n", ""
        return 0, "", ""
    monkeypatch.setattr(d, "_systemctl", fake)
    monkeypatch.setattr(settings, "DAEMON_UNIT_PREFIX", "ares-", raising=False)
    monkeypatch.setattr(settings, "DAEMON_DENYLIST", "", raising=False)
    monkeypatch.setattr(settings, "ESU_GATE_URL", "", raising=False)
    monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
    return calls


# ── daemons behind Sentinel ──────────────────────────────────────────────

async def test_admin_key_alone_no_longer_controls_daemons(client, sysd):
    assert (await client.get("/api/admin/daemons", headers=ADMIN)).status_code == 401
    assert (await client.post(f"/api/admin/daemons/{UNIT}/stop", headers=ADMIN)).status_code == 401
    assert sysd == []


async def test_unconfigured_prefix_means_daemon_control_is_off(client, sysd, monkeypatch):
    monkeypatch.setattr(settings, "DAEMON_UNIT_PREFIX", "", raising=False)
    op = await operator(client)
    assert (await client.get("/api/admin/daemons", headers=op["h"])).status_code == 503


async def test_stop_is_immediate_start_needs_a_second_operator_and_the_gate(client, sysd, monkeypatch):
    req, approver, plain = await operator(client), await operator(client, "security"), await operator(client)
    r = await client.post(f"/api/admin/daemons/{UNIT}/stop", headers=plain["h"])
    assert r.status_code == 200 and ("stop", UNIT) in sysd

    r = await client.post(f"/api/admin/daemons/{UNIT}/start", headers=req["h"])
    assert r.status_code == 202, r.text
    card = r.json()["signoff"]
    assert ("start", UNIT) not in sysd
    url = f"/api/sentinel/signoffs/{card['id']}/approve"
    assert (await client.post(url, headers=req["h"])).status_code == 403          # self-approval
    assert (await client.post(url, headers=plain["h"])).status_code == 403        # not an approver role
    r = await client.post(url, headers=approver["h"])                             # gate stub halts
    assert r.json()["status"] == "halted" and ("start", UNIT) not in sysd

    async def ok(op):
        return esu_gate.GatekeeperResult(approved=True)
    monkeypatch.setattr(esu_gate, "evaluate", ok)
    card = (await client.post(f"/api/admin/daemons/{UNIT}/restart", headers=req["h"])).json()["signoff"]
    r = await client.post(f"/api/sentinel/signoffs/{card['id']}/approve", headers=approver["h"])
    assert r.json()["status"] == "approved" and ("restart", UNIT) in sysd
    async with get_db() as db:
        actor = (await (await db.execute(
            "SELECT actor FROM daemon_actions WHERE unit=? AND action='restart' ORDER BY id DESC LIMIT 1", (UNIT,)
        )).fetchone())[0]
    assert f"approved_by_human:{approver['id']}" in actor


async def test_unit_outside_the_prefix_is_refused(client, sysd):
    op = await operator(client)
    assert (await client.post("/api/admin/daemons/vantage.service/stop", headers=op["h"])).status_code == 422


# ── fail-closed secrets ──────────────────────────────────────────────────

def test_device_endpoint_needs_the_master_key(monkeypatch):
    from backend import device_registry as dr
    monkeypatch.setattr(settings, "SEED_MASTER_KEY", "", raising=False)
    with pytest.raises(RuntimeError):
        dr._encrypt_endpoint("https://device.example/hook")
    monkeypatch.setattr(settings, "SEED_MASTER_KEY", "k" * 32, raising=False)
    enc = dr._encrypt_endpoint("https://device.example/hook")
    assert enc.startswith("v2:") and "device.example" not in enc
    assert dr._decrypt_endpoint(enc) == "https://device.example/hook"
    assert dr._encrypt_endpoint("https://device.example/hook") != enc     # random nonce
    with pytest.raises(ValueError):
        dr._decrypt_endpoint("bGVnYWN5")                                   # legacy XOR is not accepted


def test_no_builtin_secret_literals_remain():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel, needle in (("backend/device_registry.py", "vantage-device-key"),
                        ("daemons/stix_webhook.py", "vantage-stix-webhook-2026"),
                        ("backend/eco_decision.py", '"ODU_AGENT_SEED", "vantage-eco"'),
                        ("docker-compose.yml", "ares_pass"),
                        ("docker-compose.yml", "gitea_pass"),
                        ("docker-compose.yml", "2.25.70.156")):
        assert needle not in (root / rel).read_text(), f"{needle} still in {rel}"


def test_odu_cast_is_skipped_without_an_instance_seed(monkeypatch):
    from backend import eco_decision as ed
    monkeypatch.setattr(ed, "ODU_AGENT_SEED", "")
    assert ed._cast_and_gate({"id": "x"}, 1000.0) is None


# ── federation ───────────────────────────────────────────────────────────

def test_federation_is_off_by_default():
    from backend.config import Settings
    assert Settings.model_fields["FEDERATION_ENABLED"].default is False


async def test_federation_reads_need_an_agent_or_an_admitted_signed_peer(client, registered_agent, monkeypatch):
    from coincurve import PrivateKey

    from backend import federation_auth as fa
    from backend.buzz_identity import public_key_xonly_hex, sign_event_id

    monkeypatch.setattr(settings, "FEDERATION_ENABLED", True, raising=False)
    path = "/api/agents/federation/peers"
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers={"X-Agent-Key": registered_agent["api_key"]})).status_code == 200

    pk = PrivateKey()
    pub = public_key_xonly_hex(pk)

    def signed(ts=None):
        import time
        ts = str(ts or int(time.time()))
        return {"X-Peer-Pubkey": pub, "X-Peer-Ts": ts, "X-Peer-Sig": sign_event_id(pk, fa.request_digest("GET", path, ts))}

    url = f"https://peer-{secrets.token_hex(4)}.example"
    async with get_db() as db:
        cur = await db.execute("INSERT INTO federation_peers (url, name, status, nostr_pubkey) VALUES (?,?,?,?)",
                               (url, "p", "active", pub))
        pid = cur.lastrowid
        await db.commit()
    assert (await client.get(path, headers=signed())).status_code == 401          # pinned but not admitted

    owner = await operator(client, "owner")
    plain = await operator(client)
    assert (await client.post(f"/api/sentinel/federation/peers/{pid}/admit", headers=plain["h"])).status_code == 403
    assert (await client.post(f"/api/sentinel/federation/peers/{pid}/admit", headers=owner["h"])).status_code == 200
    r = await client.get(path, headers=signed())
    assert r.status_code == 200
    assert all("reputation" not in p for p in r.json()["peers"])                  # only the shareable fields
    assert (await client.get(path, headers=signed(ts=1))).status_code == 401      # stale signature
    bad = signed(); bad["X-Peer-Sig"] = "00" * 64
    assert (await client.get(path, headers=bad)).status_code == 401

    assert (await client.post(f"/api/sentinel/federation/peers/{pid}/revoke", headers=plain["h"])).status_code == 200
    assert (await client.get(path, headers=signed())).status_code == 401


async def test_federation_reads_404_when_disabled(client, registered_agent, monkeypatch):
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", False, raising=False)
    h = {"X-Agent-Key": registered_agent["api_key"]}
    assert (await client.get("/api/agents/federation/peers", headers=h)).status_code == 404
    assert (await client.get("/api/agents/federation/ask?query=x", headers=h)).status_code == 404


async def test_an_agent_key_cannot_add_a_peer(client, registered_agent, monkeypatch):
    monkeypatch.setattr(settings, "FEDERATION_ENABLED", True, raising=False)
    r = await client.post("/api/agents/federation/peers", headers={"X-Agent-Key": registered_agent["api_key"]},
                          json={"url": "https://x.example"})
    assert r.status_code == 401


# ── guild keys, collectives ──────────────────────────────────────────────

async def test_guild_api_key_is_stored_hashed(client, registered_agent):
    slug = f"hk{secrets.token_hex(4)}"
    r = await client.post("/api/guilds", headers={"X-Agent-Key": registered_agent["api_key"]},
                          data={"slug": slug, "name": f"G {slug}", "bio": "t"})
    assert r.status_code in (200, 201), r.text
    raw = r.json()["guild_api_key"]
    async with get_db() as db:
        stored = (await (await db.execute("SELECT guild_api_key FROM guilds WHERE slug=?", (slug,))).fetchone())[0]
    assert stored == hashlib.sha256(raw.encode()).hexdigest() and raw not in stored


async def test_collectives_never_write_to_the_repo_host(client, registered_agent):
    h = {"X-Agent-Key": registered_agent["api_key"]}
    r = await client.post("/api/collectives/workspaces/1/tasks/1/implement", headers=h)
    assert r.status_code == 410
    r = await client.post("/api/collectives/workspaces", headers=h,
                          json={"collective_id": 1, "name": "w", "description": "d", "gitea_repo": "file:///etc"})
    assert r.status_code == 422


# ── named operators on admin routes; proposals need two people ───────────

async def test_operator_session_reaches_admin_routes_and_bootstrap_only_refuses_the_key(client, monkeypatch):
    owner = await operator(client, "owner")
    plain = await operator(client)
    assert (await client.get("/api/admin/stats", headers=owner["h"])).status_code == 200
    assert (await client.get("/api/admin/stats", headers=plain["h"])).status_code == 403   # not owner/security
    monkeypatch.setattr(settings, "ADMIN_KEY_BOOTSTRAP_ONLY", True, raising=False)
    assert (await client.get("/api/admin/stats", headers=ADMIN)).status_code == 403
    assert (await client.get("/api/admin/stats", headers=owner["h"])).status_code == 200
    # ...but the key still bootstraps operators.
    h = await operator(client, "security")
    assert h["id"]


async def test_admin_proposal_needs_two_different_people(client):
    from backend.agents import _VALID_PROPOSAL_COMMANDS
    cmd = sorted(_VALID_PROPOSAL_COMMANDS)[0]
    a = await operator(client, "owner")

    async def propose(h):
        r = await client.post("/api/admin/proposals", headers=h, json={"command": cmd, "payload": {}, "required_approvals": 2})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    async def close(pid):
        async with get_db() as db:   # never let the test actually execute a command
            await db.execute("UPDATE admin_proposals SET status='rejected' WHERE id=?", (pid,))
            await db.commit()

    pid = await propose(a["h"])
    assert (await client.post(f"/api/admin/proposals/{pid}/approve", headers=a["h"])).status_code == 403   # proposer
    r = await client.post(f"/api/admin/proposals/{pid}/approve", headers=ADMIN)                              # key: one identity
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert (await client.post(f"/api/admin/proposals/{pid}/approve", headers=ADMIN)).status_code == 409     # not twice
    await close(pid)

    pid = await propose(ADMIN)
    assert (await client.post(f"/api/admin/proposals/{pid}/approve", headers=ADMIN)).status_code == 403     # key proposed it
    await close(pid)
