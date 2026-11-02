"""
mood_store.py — persistence for the mood engine.

SCHEMA MOOD_V1 (this is the answer to "is it already serializable")

The five floats ARE JSON-native. Four other things are NOT representable in the
current in-memory object and would corrupt state on restart, so they are part of
the schema:

  1. rng_state    The private noise stream. Without it, a restart rewinds the
                  agent's endogenous randomness and it literally repeats itself
                  — the opposite of alive.
  2. last_tick_at Wall-clock at last update. Mood decays over downtime; without
                  a timestamp, an agent that slept a week wakes exactly as it
                  was, which reads as a save file rather than a mind.
  3. fatigue_age  Per-dimension fatigue decays with time. Stored as the value
                  AT last_tick_at so it can be aged forward deterministically.
  4. history      Currently an unbounded list -> the one real leak. Capped at
                  HISTORY_CAP and persisted as a ring buffer.

Everything else (m, fatigue, refractory, epoch) is plain float/int.
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import asdict

SCHEMA = 1
HISTORY_CAP = 64

DIMS = ("valence", "arousal", "focus", "trust", "tension")


def to_dict(mood, history_cap: int = HISTORY_CAP) -> dict:
    """Serialise a Mood. Python floats are JSON-native; rng state is not."""
    rng_state = mood._rng.getstate()          # (version, tuple[int], gauss_next)
    return {
        "v": SCHEMA,
        "agent_id": mood.agent_id,
        "seed_commitment": mood.seed.hex()[:16],   # commitment only, never the seed
        "epoch": mood.epoch,
        "m": {d: float(mood.m[d]) for d in DIMS},
        "fatigue": {d: float(mood.fatigue[d]) for d in DIMS},
        "refractory": float(mood.refractory),
        "rng_state": [rng_state[0], list(rng_state[1]), rng_state[2]],
        "last_tick_at": float(getattr(mood, "last_tick_at", time.time())),
        "history": [{d: float(v) for d, v in h.items()} for h in mood.history[-history_cap:]],
        "tick_count": int(getattr(mood, "tick_count", len(mood.history))),
    }


def from_dict(d: dict, MoodCls, seed: bytes):
    """Rebuild a Mood exactly. Age fatigue forward for time spent offline."""
    if d.get("v") != SCHEMA:
        raise ValueError(f"mood schema mismatch: {d.get('v')} != {SCHEMA}")

    mo = MoodCls(agent_id=d["agent_id"], seed=seed)
    mo.m = {k: float(v) for k, v in d["m"].items()}
    mo.fatigue = {k: float(v) for k, v in d["fatigue"].items()}
    mo.refractory = float(d["refractory"])
    mo.epoch = int(d["epoch"])
    mo.history = list(d.get("history", []))
    mo.tick_count = int(d.get("tick_count", len(mo.history)))
    mo.last_tick_at = float(d.get("last_tick_at", time.time()))

    st = d.get("rng_state")
    if st:
        mo._rng.setstate((st[0], tuple(st[1]), st[2]))

    # offline ageing: fatigue relaxes while nobody is watching
    offline_days = max(0.0, (time.time() - mo.last_tick_at) / 86400.0)
    if offline_days > 0:
        decay = pow(0.5, offline_days)      # fatigue halves ~daily
        for k in mo.fatigue:
            mo.fatigue[k] *= decay
    return mo


# ---------------------------------------------------------------- store

class MoodStore:
    """One JSON doc per agent. Deliberately dumb — no ORM, no migration engine.

    Path: <root>/<agent_id>.json
    Swap `_read`/`_write` for minipae's mem/card/<agent-id>/mood when layer-3 is wired.
    """

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, agent_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in agent_id)
        return os.path.join(self.root, f"{safe}.json")

    def load(self, agent_id: str, MoodCls, seed: bytes):
        p = self._path(agent_id)
        if not os.path.exists(p):
            mo = MoodCls(agent_id=agent_id, seed=seed)
            mo.last_tick_at = time.time()
            mo.tick_count = 0
            return mo
        with open(p, "r", encoding="utf-8") as f:
            return from_dict(json.load(f), MoodCls, seed)

    def save(self, mood) -> str:
        p = self._path(mood.agent_id)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(to_dict(mood), f, separators=(",", ":"))
        os.replace(tmp, p)          # atomic; a crash mid-write cannot corrupt
        return p

    def exists(self, agent_id: str) -> bool:
        return os.path.exists(self._path(agent_id))
