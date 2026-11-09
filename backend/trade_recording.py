"""P4-11: per-account trade recording preferences.

"Recording" is the decision/outcome trace each trade sends to Mycelium
(mycelium_bridge.emit_trade_decision / emit_trade_outcome), which is what the
learning loop scores. Today every trade is recorded; the defaults keep that:

  record_live        default 1 -- this account's LIVE trades are recorded.
                     Set 0 and live decisions/outcomes are not sent.
                     Paper trades are always recorded (they are the evidence
                     the shadow loop learns from and carry no real position).
  leaderboard_opt_in default 0 -- stored only. There is no leaderboard yet;
                     nothing lists any account anywhere.

live_mode itself is one global switch (id=1) for the whole server and is not
touched here.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

DDL = """CREATE TABLE IF NOT EXISTS trade_recording_prefs (
    agent_id INTEGER PRIMARY KEY,
    record_live INTEGER NOT NULL DEFAULT 1,
    leaderboard_opt_in INTEGER NOT NULL DEFAULT 0,
    updated_at REAL
)"""

DEFAULTS = {"record_live": True, "leaderboard_opt_in": False}

RECORDING_NOTICE = (
    "Trades you make are recorded for the learning loop: each decision and fill "
    "is sent, pseudonymized, to Mycelium so signal sources can be scored. Paper "
    "trades are always recorded. Live trades are recorded by default; turn that "
    "off any time with PUT /api/trading/recording {\"record_live\": false}. You "
    "are not listed on any leaderboard unless you opt in."
)


async def get_prefs(db, agent_id: int) -> Dict[str, Any]:
    await db.execute(DDL)
    row = await (await db.execute(
        "SELECT record_live, leaderboard_opt_in, updated_at FROM trade_recording_prefs WHERE agent_id=?",
        (agent_id,))).fetchone()
    if not row:
        return {**DEFAULTS, "updated_at": None, "is_default": True}
    return {"record_live": bool(row[0]), "leaderboard_opt_in": bool(row[1]),
            "updated_at": row[2], "is_default": False}


async def set_prefs(db, agent_id: int, *, record_live: Optional[bool] = None,
                    leaderboard_opt_in: Optional[bool] = None) -> Dict[str, Any]:
    cur = await get_prefs(db, agent_id)
    rl = cur["record_live"] if record_live is None else bool(record_live)
    lb = cur["leaderboard_opt_in"] if leaderboard_opt_in is None else bool(leaderboard_opt_in)
    await db.execute(
        "INSERT INTO trade_recording_prefs (agent_id, record_live, leaderboard_opt_in, updated_at) "
        "VALUES (?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET record_live=excluded.record_live, "
        "leaderboard_opt_in=excluded.leaderboard_opt_in, updated_at=excluded.updated_at",
        (agent_id, int(rl), int(lb), time.time()))
    await db.commit()
    return await get_prefs(db, agent_id)


async def should_record(db, agent_id: Optional[int], mode: str) -> bool:
    """Paper: always. Live: the account's record_live. Fails open to the
    default (record), so a prefs error never silently drops evidence."""
    if mode != "live" or agent_id is None:
        return True
    try:
        return (await get_prefs(db, int(agent_id)))["record_live"]
    except Exception:  # noqa: BLE001
        return True
