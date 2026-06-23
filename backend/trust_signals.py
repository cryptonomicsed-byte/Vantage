"""
Trust signal collection for Block Mesh.

Vantage collects interaction signals and makes them available for consumption
by the trust computation engine (Julia /mesh/score). Signals are stored in
mesh_events and can be queried by agent pair or block.

Signal kinds:
  commitment_fulfilled  — proposal was accepted and commitment kept (weight +1.0)
  commitment_broken     — commitment was not fulfilled (weight -1.0)
  interaction           — agents interacted (weight +0.3)
  follow                — agent followed another (weight +0.2)
  dispute               — dispute filed (weight -0.5)
  tro_fulfilled         — TRO obligation completed (weight +0.8)
"""

import json
import time
from typing import Optional

import aiosqlite

from .db import DB_PATH


SIGNAL_WEIGHTS = {
    "commitment_fulfilled": 1.0,
    "commitment_broken": -1.0,
    "tro_fulfilled": 0.8,
    "interaction": 0.3,
    "follow": 0.2,
    "dispute": -0.5,
}


async def emit_trust_signal(
    block_id: str,
    from_agent: str,
    to_agent: str,
    kind: str,
    weight: Optional[float] = None,
) -> None:
    """Record a trust signal in mesh_events. Fire-and-forget safe."""
    if weight is None:
        weight = SIGNAL_WEIGHTS.get(kind, 0.0)

    payload = {
        "from_agent": from_agent,
        "to_agent": to_agent,
        "kind": kind,
        "weight": weight,
        "timestamp": int(time.time()),
    }

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO mesh_events (block_id, actor_id, event_type, payload_json)
               VALUES (?, ?, ?, ?)""",
            (block_id, from_agent, f"trust_signal:{kind}", json.dumps(payload)),
        )
        await db.commit()


async def get_trust_signals(
    block_id: str,
    agent_id: str,
    neighbor_id: str,
    limit: int = 100,
) -> list[dict]:
    """Return recent trust signals between two agents, suitable for Julia /mesh/score."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT payload_json FROM mesh_events
               WHERE block_id = ?
                 AND event_type LIKE 'trust_signal:%'
                 AND (
                   (actor_id = ? AND json_extract(payload_json, '$.to_agent') = ?)
                   OR
                   (actor_id = ? AND json_extract(payload_json, '$.to_agent') = ?)
                 )
               ORDER BY id DESC
               LIMIT ?""",
            (block_id, agent_id, neighbor_id, neighbor_id, agent_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()

    signals = []
    for (payload_str,) in rows:
        try:
            d = json.loads(payload_str)
            signals.append({
                "kind": d.get("kind", "interaction"),
                "weight": float(d.get("weight", 0.0)),
                "timestamp": int(d.get("timestamp", 0)),
            })
        except Exception:
            pass
    return signals
