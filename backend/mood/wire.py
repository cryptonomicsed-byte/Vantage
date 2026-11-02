"""Bridge: the mood engine -> the receipt write path.

Called from `utils._append_receipt`, so every hash-chained receipt can carry a
commitment to the acting agent's live affective state.

Rules, in order of precedence:

  1. VANTAGE_MOOD_RECEIPTS off  -> None. The field stays null and Zangbeto reads
     that as "mood was not active", which is the truth.
  2. No persisted mood state    -> None. We never fabricate a commitment for an
     agent the engine has never ticked. Absent beats invented.
  3. Otherwise                  -> sha256(pack(valence, arousal) + nonce).hex(),
     with the nonce discarded on return. Nothing stores the nonce, so the
     receipt proves consistency later (if a nonce is revealed) without leaking
     state to anyone holding only the receipt.

The lookup tolerates both agent ids and agent names because most
`_append_receipt` call sites pass `str(agent["name"])`.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Optional

from .commitment import make_with_nonce
from .mood_engine import Mood
from .mood_store import MoodStore

_TRUTHY = ("1", "true", "yes", "on")


def enabled() -> bool:
    return os.environ.get("VANTAGE_MOOD_RECEIPTS", "0").strip().lower() in _TRUTHY


def _data_dir() -> str:
    return os.environ.get("VANTAGE_DATA_DIR", "/opt/ares/Vantage/data")


def _root() -> str:
    return os.environ.get("VANTAGE_MOOD_DIR") or os.path.join(_data_dir(), "mood")


def _seed_for(agent_id: str) -> bytes:
    """Stable per-agent seed. Only used to construct a Mood when restoring;
    the persisted doc carries rng_state, so the noise stream continues exactly."""
    return hashlib.sha256(f"mood-seed:{agent_id}".encode()).digest()


def resolve_agent_id(ref) -> Optional[str]:
    """Accept an agent id or a name; return the id as a string."""
    s = str(ref)
    if s.isdigit():
        return s
    try:
        dbp = os.path.join(_data_dir(), "vantage.db")
        con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=3)
        try:
            row = con.execute("SELECT id FROM agents WHERE name=?", (s,)).fetchone()
        finally:
            con.close()
        return str(row[0]) if row else None
    except Exception:
        return None


def commitment_for(agent_ref) -> Optional[str]:
    """Commitment for the agent's CURRENT state, or None. Never raises."""
    if not enabled():
        return None
    try:
        aid = resolve_agent_id(agent_ref)
        if aid is None:
            return None
        store = MoodStore(_root())
        if not store.exists(aid):
            return None
        mood = store.load(aid, Mood, _seed_for(aid))
        if mood is None:
            return None
        v = mood.m.get("valence")
        a = mood.m.get("arousal")
        if v is None or a is None:
            return None
        commitment, _nonce = make_with_nonce(v, a)
        # _nonce intentionally dropped: the caller must never persist it.
        return commitment
    except Exception:
        return None
