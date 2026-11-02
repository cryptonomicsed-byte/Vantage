"""
hermetic_scorer.py — the 7 principles as computed numbers, not constants.

Every principle returns:
    score      float 0..1, or None if there is no evidence
    source     which artifact produced it
    evidence   the raw quantity (so a human can audit the number)
    n          sample size

Contract: INSUFFICIENT DATA FAILS CLOSED. A principle with no evidence returns
score=None, and the weighted alignment is computed over *present* principles
only, with `coverage` reported. That is deliberate: a defaulted measurement of
0.88 that clears a gate is the exact defect this codebase already got burned by
(server.jl:247, f1 default 0.88 clearing a 0.777 threshold). Absent evidence
must not look like good evidence.

Three principles are mood-backed and cost nothing once mood persists:
    Vibration = mood.arousal
    Polarity  = 1 - |mood.valence|          (distance from centre)
    Rhythm    = 1 - fatigue[action_type]    (saturation on this stimulus)
The other four read receipts / jobs / skill_badges / Koodu.
"""

from __future__ import annotations
import json
import math
import sqlite3

WEIGHTS = {
    "mentalism": 0.20,
    "correspondence": 0.15,
    "vibration": 0.15,
    "polarity": 0.10,
    "rhythm": 0.10,
    "cause-effect": 0.15,
    "gender": 0.15,
}

KOODU_FIT = {
    # domain -> set of Koodu days (0..6) where that work is in season.
    # Placeholder lattice: replace with Koodu/spiral-calendar.js SPIRAL_PHASES.
    "code":     {0, 3, 5},
    "trade":    {1, 4, 5},
    "video":    {2, 3, 6},
    "research": {3, 6},
    "ops":      {0, 5, 6},
}


def _p(score, source, evidence, n=1):
    return {"score": None if score is None else round(max(0.0, min(1.0, score)), 4),
            "source": source, "evidence": evidence, "n": n}


# ------------------------------------------------------------------ the seven

def _tokens(x) -> set:
    """Normalise str OR list into a token set. A list stringified with str()
    produces "['code', 'index']" whose tokens never match a clean "code" --
    that bug scored a mismatched agent 1.0."""
    if isinstance(x, (list, tuple, set)):
        out = set()
        for item in x:
            out |= {t for t in str(item).lower().replace(",", " ").split() if t}
        return out
    return {t for t in str(x).lower().replace(",", " ").split() if t}


def mentalism(conn, agent_id, intent, capability) -> dict:
    """Intent-action coherence: does the capability used match what was asked?"""
    if not intent or not capability:
        return _p(None, "none", "no intent/capability", 0)
    a, b = _tokens(intent), _tokens(capability)
    if not a or not b:
        return _p(None, "none", "empty tokens", 0)
    j = len(a & b) / len(a | b)          # token Jaccard; swap for embeddings later
    return _p(j, "text:jaccard(intent,action)", {"intent": sorted(a), "used": sorted(b)})


def correspondence(conn, agent_id, action_type, hive_mode) -> dict:
    """Agent<->hive alignment: is this agent doing what the hive is doing?"""
    if not hive_mode:
        row = conn.execute(
            "SELECT job_type, COUNT(*) c FROM jobs WHERE status='done' "
            "GROUP BY job_type ORDER BY c DESC LIMIT 1").fetchone()
        hive_mode = row[0] if row else None
    if not action_type or not hive_mode:
        return _p(None, "none", "no dominant hive mode", 0)
    return _p(1.0 if action_type == hive_mode else 0.25,
              "db:jobs.dominant_type", {"action": action_type, "hive": hive_mode})


def vibration(conn, agent_id, mood) -> dict:
    """Momentum / flow state. Mood-backed when available, else success rate."""
    if mood is not None:
        return _p(mood.m["arousal"], "mood:arousal",
                  {"arousal": round(mood.m["arousal"], 3)})
    row = conn.execute(
        "SELECT COUNT(*) tot, SUM(status='done') ok FROM jobs WHERE poster_id=?",
        (agent_id,)).fetchone()
    tot, ok = (row or (0, 0))
    if not tot:
        return _p(None, "none", "no jobs and no mood", 0)
    return _p((ok or 0) / tot, "db:jobs.success_rate", {"done": ok, "total": tot}, tot)


def polarity(conn, agent_id, mood) -> dict:
    """Capability balance. Mood-backed: 1-|valence| = centred, not extreme."""
    if mood is not None:
        return _p(1.0 - abs(mood.m["valence"]), "mood:1-|valence|",
                  {"valence": round(mood.m["valence"], 3)})
    row = conn.execute("SELECT skill_badges FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not row or not row[0]:
        return _p(None, "none", "no capability histogram", 0)
    try:
        hist = list(json.loads(row[0]).values())
    except Exception:
        return _p(None, "none", "unparseable skill_badges", 0)
    if not hist or sum(hist) == 0:
        return _p(None, "none", "empty histogram", 0)
    return _p(1.0 - _gini(hist), "db:skill_badges.gini", {"hist": hist})


def rhythm(conn, agent_id, action_type, mood, koodu_day) -> dict:
    """Epoch fit x stimulus saturation. Fast term (mood) + slow term (Koodu)."""
    slow = None
    if koodu_day is not None and action_type in KOODU_FIT:
        slow = 1.0 if koodu_day in KOODU_FIT[action_type] else 0.3
    fast = None
    if mood is not None:
        fast = 1.0 - mood.fatigue.get(action_type, 0.0)
        fast = max(0.0, min(1.0, fast))
    if slow is None and fast is None:
        return _p(None, "none", "no koodu epoch and no fatigue", 0)
    if slow is None: return _p(fast, "mood:1-fatigue", {"fatigue_fast": fast})
    if fast is None: return _p(slow, "koodu:epoch_fit", {"koodu_day": koodu_day})
    return _p(0.5 * slow + 0.5 * fast, "koodu×mood",
              {"koodu_fit": slow, "fatigue_free": round(fast, 3)})


def cause_effect(conn, agent_id) -> dict:
    """Receipt chain completeness: does the record explain the outcome?"""
    row = conn.execute(
        "SELECT COUNT(*) FROM receipts WHERE agent_id=?", (agent_id,)).fetchone()
    n = row[0] if row else 0
    if n == 0:
        return _p(None, "none", "zero receipts for this agent", 0)
    linked = conn.execute(
        "SELECT COUNT(*) FROM receipts WHERE agent_id=? AND previous_hash IS NOT NULL "
        "AND previous_hash != ''", (agent_id,)).fetchone()[0]
    return _p(linked / n, "db:receipts.chain_linkage",
              {"linked": linked, "total": n}, n)


def gender(conn, agent_id) -> dict:
    """Generative/receptive balance: work produced vs value received."""
    done = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE poster_id=? AND status='done'", (agent_id,)).fetchone()[0]
    row = conn.execute("SELECT reputation FROM agents WHERE id=?", (agent_id,)).fetchone()
    rep = (row[0] or 0) if row else 0
    if done == 0 and not rep:
        return _p(None, "none", "no work and no reputation", 0)
    return _p(done / (done + max(0.0, float(rep or 0)) + 1.0),
              "db:jobs.generative_vs_received",
              {"jobs_done": done, "reputation": rep})


# ------------------------------------------------------------------ assembly

def score_agent(db_path, agent_id, *, intent=None, capability=None,
                action_type=None, mood=None, koodu_day=None) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        parts = {
            "mentalism":     mentalism(conn, agent_id, intent, capability),
            "correspondence": correspondence(conn, agent_id, action_type, None),
            "vibration":     vibration(conn, agent_id, mood),
            "polarity":      polarity(conn, agent_id, mood),
            "rhythm":        rhythm(conn, agent_id, action_type, mood, koodu_day),
            "cause-effect":  cause_effect(conn, agent_id),
            "gender":        gender(conn, agent_id),
        }
    finally:
        conn.close()

    present = {k: v for k, v in parts.items() if v["score"] is not None}
    wsum = sum(WEIGHTS[k] for k in present)
    alignment = (sum(WEIGHTS[k] * v["score"] for k, v in present.items()) / wsum
                 if wsum else None)

    return {
        "agent_id": agent_id,
        "alignment": None if alignment is None else round(alignment, 4),
        "coverage": round(wsum, 3),          # fraction of weight backed by evidence
        "principles": parts,
        "mood_backed": [k for k, v in parts.items() if str(v["source"]).startswith("mood")],
    }


def _gini(xs):
    xs = sorted(float(x) for x in xs)
    n = len(xs)
    if n == 0 or sum(xs) == 0:
        return 1.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2 * cum) / (n * sum(xs)) - (n + 1) / n
