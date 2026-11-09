"""P3-09: buzz_engrams / trace_engram via minipae, BUZZ_ENGRAMS_LEGACY=1 keeps
the old path. Wire compatibility is checked against the real minipae module
when it is importable (the box checkout); routing is checked everywhere."""
import json
import os
import secrets

import pytest
from coincurve import PrivateKey, PublicKeyXOnly

from backend import buzz_engrams as be
from backend import nip44
from backend.buzz_identity import public_key_xonly_hex

MP = be._minipae()
needs_minipae = pytest.mark.skipif(MP is None, reason="minipae not importable here")


def _pk():
    return PrivateKey(secrets.token_bytes(32))


@needs_minipae
def test_wire_identical_dtag_and_both_way_decrypt():
    agent, owner = _pk(), _pk()
    owner_hex = public_key_xonly_hex(owner)
    ck_v = nip44.get_conversation_key(agent, owner_hex)
    ck_m = MP.conversation_key(agent.secret, bytes.fromhex(owner_hex))
    assert ck_v == ck_m
    slug = "mem/vantage/test-ẹ̀kọ́"
    assert be._derive_d_tag(ck_v, slug) == MP.d_tag(slug, ck_m)
    body = json.dumps({"slug": slug, "value": "Òrìṣà"}, separators=(",", ":"))
    assert nip44.decrypt(MP.nip44_encrypt(body, ck_m), ck_v) == body      # minipae -> legacy
    assert MP.nip44_decrypt(nip44.encrypt(body, ck_v), ck_m) == body      # legacy -> minipae


@needs_minipae
def test_minipae_event_verifies_independently():
    agent, owner = _pk(), _pk()
    ev = MP.build_event("mem/x", {"slug": "mem/x", "value": "v"}, agent.secret,
                        bytes.fromhex(public_key_xonly_hex(owner)))
    assert ev["kind"] == be.KIND_ENGRAM and ev["pubkey"] == public_key_xonly_hex(agent)
    assert PublicKeyXOnly(bytes.fromhex(ev["pubkey"])).verify(bytes.fromhex(ev["sig"]), bytes.fromhex(ev["id"]))


class FakeWS:
    def __init__(self, log):
        self.log = log

    async def send(self, raw):
        self.log.append(("raw", json.loads(raw)))


class FakeSession:
    log = []

    def __init__(self, url, pk):
        self.ws = FakeWS(FakeSession.log)

    async def connect(self):
        pass

    async def authenticate(self):
        pass

    async def close(self):
        pass

    async def _recv_json(self, timeout=10):
        ev = FakeSession.log[-1][1][1]
        return ["OK", ev["id"], True, ""]

    async def publish(self, kind, content, tags=None):
        FakeSession.log.append(("publish", kind))
        return {"event": {"id": "legacy-id"}, "ack": ["OK", "legacy-id", True, ""]}


@pytest.fixture
def wired(monkeypatch):
    agent, owner = _pk(), _pk()

    async def a(_):
        return agent

    async def o(*_):
        return owner
    monkeypatch.setattr(be, "derive_buzz_keypair", a)
    monkeypatch.setattr(be, "derive_instance_keypair", o)
    monkeypatch.setattr(be, "BuzzSession", FakeSession)
    FakeSession.log.clear()
    for k in ("BUZZ_ENGRAMS_VIA_MINIPAE", "BUZZ_ENGRAMS_LEGACY"):
        monkeypatch.delenv(k, raising=False)
    return agent, owner


@pytest.mark.asyncio
async def test_default_is_the_legacy_path(wired):
    out = await be.write_engram(1, "mem/a", "v")
    assert out["ok"] and "via" not in out and FakeSession.log[0][0] == "publish"


@needs_minipae
@pytest.mark.asyncio
async def test_flag_on_uses_minipae_with_same_dtag(wired, monkeypatch):
    agent, owner = wired
    monkeypatch.setenv("BUZZ_ENGRAMS_VIA_MINIPAE", "1")
    out = await be.write_engram(1, "mem/a", "v")
    assert out["ok"] and out["via"] == "minipae"
    ev = FakeSession.log[0][1][1]
    ck = nip44.get_conversation_key(agent, public_key_xonly_hex(owner))
    assert out["d_tag"] == be._derive_d_tag(ck, "mem/a")              # same address as legacy
    assert json.loads(nip44.decrypt(ev["content"], ck)) == {"slug": "mem/a", "value": "v"}


@pytest.mark.asyncio
async def test_legacy_override_and_fallback(wired, monkeypatch):
    monkeypatch.setenv("BUZZ_ENGRAMS_VIA_MINIPAE", "1")
    monkeypatch.setenv("BUZZ_ENGRAMS_LEGACY", "1")
    assert not be.use_minipae()
    out = await be.write_engram(1, "mem/a", "v")
    assert out["ok"] and FakeSession.log[0][0] == "publish"
    # minipae present but failing -> legacy still writes
    monkeypatch.delenv("BUZZ_ENGRAMS_LEGACY")
    FakeSession.log.clear()

    class Broken:
        @staticmethod
        def build_event(*a, **k):
            raise RuntimeError("boom")
    monkeypatch.setattr(be, "_minipae", lambda: Broken)
    out = await be.write_engram(1, "mem/a", "v")
    assert out["ok"] and FakeSession.log[0][0] == "publish"


@needs_minipae
def test_trace_engram_built_and_signed_by_minipae():
    from backend import trace_engram as te
    agent, owner = _pk(), _pk()
    recs = [{"ts": 1762600000, "tool": "search", "args": {"q": "x"}, "result": "ok", "duration_ms": 5}]
    ev = te.build_signed_engram(recs, agent="Hermes", session="s1", seckey=agent.secret,
                                owner_pubkey_hex=public_key_xonly_hex(owner))
    ck = nip44.get_conversation_key(agent, public_key_xonly_hex(owner))
    body = json.loads(nip44.decrypt(ev["content"], ck))
    assert body["slug"] == "mem/trace/hermes/s1"
    assert json.loads(body["value"]) == te.to_engram(recs, agent="Hermes", session="s1")
    assert te.build_signed_engram(recs, agent="!!", session="s", seckey=agent.secret,
                                  owner_pubkey_hex=public_key_xonly_hex(owner)) is None
