"""P3-06: guild reliability must match bondhive core/src/score.rs exactly.

GOLDEN values below were produced by compiling bondhive's score.rs unmodified
(rustc, `#[path]` include of core/src/score.rs) and printing
`compute_bond_score(...)` with `{:?}` and `f64::to_bits`. The first seven
vectors are score.rs's own `mod tests` histories; the rest exercise the
half-life, the final-only clamp, the t=0 and out-of-order quirks.
Assertions are bit-exact (`float.hex`/struct bits), not approximate.
"""
import hashlib
import secrets
import struct

import aiosqlite
import pytest
import pytest_asyncio

from backend import guild_reliability as gr
from backend.db import DB_PATH

NOW = 1_800_000_000
D = 86_400
S, F, X = "success", "failure", "fraud"


def _bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def _clamp_hi_history():
    h = [(S, NOW - 1), (S, NOW - 2)]
    h += [(S, NOW - 10_000 + i) for i in range(200)]
    return h


# name -> (history, now, expected f64 bits from score.rs)
GOLDEN = {
    # ── score.rs mod tests ─────────────────────────────────────────────
    "newborn": ([], NOW, 4632233691727265792),                       # 50.0
    "mixed": ([(S, NOW - 1000), (S, NOW - 700), (F, NOW - 400), (S, NOW - 100)], NOW,
              4632374422943165611),                                  # 50.99995543152303
    "fraud": ([(X, NOW - 100)], NOW, 4631530007421792388),            # 45.000022284774815
    "fail": ([(F, NOW - 100)], NOW, 4631952218005076430),             # 48.00000891390992
    "stale": ([(S, NOW - 5 * 365 * D), (S, NOW - 5 * 365 * D + 1)], NOW,
              4632233941376433941),                                  # 50.00177386402917
    "clamp_hi": (_clamp_hi_history(), NOW, 4636737291354636288),      # 100.0
    "clamp_lo": ([(X, NOW - 1), (X, NOW - 2), (X, NOW - 3)], NOW,
                 4630122629684203774),                               # 35.00000200563407
    # ── extra vectors ──────────────────────────────────────────────────
    "one_halflife": ([(S, NOW - 180 * D)], NOW, 4632304060471443456),          # 50.5
    "two_halflife_fraud": ([(X, NOW - 360 * D)], NOW, 4632057769866821632),    # 48.75
    "at_now": ([(S, NOW)], NOW, 4632374429215621120),                          # 51.0
    "t_zero_event": ([(S, 0), (S, 100)], 100, 4632515166703976448),            # 52.0
    "out_of_order": ([(S, NOW - 10), (F, NOW - 1000)], NOW, 4632092960511391133),  # 49.000044568655746
    "now_before_last": ([(X, NOW)], NOW - 500, 4631530004285489152),           # 45.0
    "twelve_fraud": ([(X, NOW - 100 + i) for i in range(12)], NOW, 0),         # 0.0
    "twelve_fraud_then_success": ([(X, NOW - 100 + i) for i in range(12)] + [(S, NOW)], NOW, 0),
    "sixty_success_30d": ([(S, NOW - 30 * D + i) for i in range(60)], NOW, 4636737291354636288),
    "long_mixed": ([(S, NOW - 400 * D), (X, NOW - 200 * D), (S, NOW - 90 * D), (F, NOW - 7 * D),
                    (S, NOW - D)], NOW, 4631903814159417817),        # 47.65607890475412
}


@pytest.mark.parametrize("name", list(GOLDEN))
def test_matches_score_rs_bit_exact(name):
    history, now, bits = GOLDEN[name]
    got = gr.compute_bond_score(history, now)
    assert _bits(got) == bits, f"{name}: got {got!r} ({_bits(got)}), score.rs bits {bits}"


# score.rs's own inequality assertions, kept verbatim in spirit
def test_score_rs_assertions():
    assert gr.compute_bond_score([], NOW) == 50.0
    m = gr.compute_bond_score(GOLDEN["mixed"][0], NOW)
    assert 50.0 < m < 100.0
    assert gr.compute_bond_score([(X, NOW - 100)], NOW) < gr.compute_bond_score([(F, NOW - 100)], NOW)
    assert abs(gr.compute_bond_score(GOLDEN["stale"][0], NOW) - 50.0) < 1.0
    assert gr.compute_bond_score(_clamp_hi_history(), NOW) <= 100.0
    assert gr.compute_bond_score(GOLDEN["clamp_lo"][0], NOW) >= 0.0


def test_discount_curve_and_premium():
    for s, d in [(50.0, 0.0), (59.999, 0.0), (60.0, 0.1), (69.9, 0.1), (70.0, 0.25), (75.0, 0.25),
                 (79.99, 0.25), (80.0, 0.5), (100.0, 0.5)]:
        assert gr.premium_discount(s) == d
    assert gr.effective_premium(300, 75.0) == 225
    assert gr.effective_premium(300, 50.0) == 300


def test_outcome_counts_and_verdicts():
    c = gr.outcome_counts([(S, 1), (S, 2), (F, 3), (X, 4)])
    assert c == {"success": 2, "failure": 1, "fraud": 1, "total": 4}
    assert gr.outcome_from_verdict("pass", None) == S
    assert gr.outcome_from_verdict("fail", None) == F
    assert gr.outcome_from_verdict("x", None) is None
    assert gr.outcome_from_verdict("pass", "uphold") == F
    assert gr.outcome_from_verdict("fail", "reject") == S
    assert gr.outcome_from_verdict("pass", "split") == F
    assert gr.outcome_from_verdict("x", "other") is None


def test_unknown_outcome_rejected():
    with pytest.raises(ValueError):
        gr.compute_bond_score([("bogus", NOW)], NOW)


# ── endpoint ───────────────────────────────────────────────────────────────────

async def _agent(db, guild_id=None):
    raw = f"vantage_{secrets.token_hex(16)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    name = f"Rel_{hashed[:10]}"
    cur = await db.execute("INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)", (name, hashed, "t"))
    aid = cur.lastrowid
    if guild_id is not None:
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'member')",
                         (guild_id, aid, name))
    return {"id": aid, "headers": {"X-Agent-Key": raw}}


@pytest_asyncio.fixture
async def space(client):
    async with aiosqlite.connect(DB_PATH) as db:
        owner = await _agent(db)
        slug = f"rel-{secrets.token_hex(4)}"
        cur = await db.execute(
            "INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key) VALUES (?,?,?,?,?,?)",
            (slug, f"G_{slug}", "t", owner["id"], "owner", f"vantage_guild_{secrets.token_hex(24)}"))
        gid = cur.lastrowid
        await db.execute("INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
                         (gid, owner["id"], "owner"))
        a = await _agent(db, gid)
        b = await _agent(db, gid)
        outsider = await _agent(db)
        await db.commit()
    r = await client.post(f"/api/guilds/{slug}/trading-spaces", data={"name": "S"}, headers=owner["headers"])
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    return {"slug": slug, "sid": sid, "base": f"/api/guilds/{slug}/trading-spaces/{sid}/reliability",
            "owner": owner, "a": a, "b": b, "outsider": outsider}


@pytest.mark.asyncio
async def test_empty_space_and_neutral_agent(client, space):
    r = await client.get(space["base"], headers=space["a"]["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agents"] == [] and body["neutral_score"] == 50.0 and body["half_life_days"] == 180.0
    r = await client.get(space["base"], params={"agent_id": space["a"]["id"]}, headers=space["a"]["headers"])
    assert r.json()["agents"][0]["score"] == 50.0


@pytest.mark.asyncio
async def test_outcomes_drive_score_and_tables(client, space):
    owner = space["owner"]["headers"]
    for o in (S, S, F):
        r = await client.post(space["base"] + "/outcomes", data={"agent_id": space["a"]["id"], "outcome": o},
                              headers=owner)
        assert r.status_code == 200, r.text
    r = await client.post(space["base"] + "/outcomes", data={"agent_id": space["b"]["id"], "outcome": X},
                          headers=owner)
    assert r.status_code == 200, r.text

    r = await client.get(space["base"], headers=space["b"]["headers"])
    assert r.status_code == 200, r.text
    agents = {row["agent_id"]: row for row in r.json()["agents"]}
    a, b = agents[space["a"]["id"]], agents[space["b"]["id"]]
    assert 49.99 < a["score"] <= 50.0 and a["outcomes"] == {"success": 2, "failure": 1, "fraud": 0, "total": 3}
    assert 45.0 <= b["score"] < 45.01
    assert r.json()["agents"][0]["agent_id"] == space["a"]["id"]  # sorted by score desc

    # The endpoint result equals the pure port over the stored history.
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT outcome, at FROM guild_agent_outcomes WHERE guild_slug=? AND space_id=? AND agent_id=? "
            "ORDER BY at, id", (space["slug"], space["sid"], space["a"]["id"])) as cur:
            hist = [(o, t) for o, t in await cur.fetchall()]
        async with db.execute(
            "SELECT score, success, failure, fraud FROM guild_agent_scores WHERE guild_slug=? AND space_id=? "
            "AND agent_id=?", (space["slug"], space["sid"], space["a"]["id"])) as cur:
            cached = await cur.fetchone()
    assert len(hist) == 3
    assert cached[1:] == (2, 1, 0)
    assert gr.compute_bond_score(hist, a["computed_at"]) == a["score"] == cached[0]


@pytest.mark.asyncio
async def test_access_rules(client, space):
    base = space["base"]
    assert (await client.get(base, headers=space["outsider"]["headers"])).status_code == 403
    r = await client.get(f"/api/guilds/{space['slug']}/trading-spaces/nope/reliability",
                         headers=space["a"]["headers"])
    assert r.status_code == 404
    # only the owner records; not for themself; target must be a member; outcome validated
    r = await client.post(base + "/outcomes", data={"agent_id": space["b"]["id"], "outcome": X},
                          headers=space["a"]["headers"])
    assert r.status_code == 403
    r = await client.post(base + "/outcomes", data={"agent_id": space["owner"]["id"], "outcome": S},
                          headers=space["owner"]["headers"])
    assert r.status_code == 403
    r = await client.post(base + "/outcomes", data={"agent_id": space["outsider"]["id"], "outcome": S},
                          headers=space["owner"]["headers"])
    assert r.status_code == 404
    r = await client.post(base + "/outcomes", data={"agent_id": space["a"]["id"], "outcome": "great"},
                          headers=space["owner"]["headers"])
    assert r.status_code == 422
