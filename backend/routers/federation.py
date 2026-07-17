"""Federation galaxy — merged multi-agent view."""
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Header, Query

from ..db import DB_PATH, get_db
from ..memory_vault import MemoryVault
from ..routers.memory_vault import _resolve_accessor

router = APIRouter(prefix="/api/federation", tags=["federation"])

_AGENT_COLORS = [
    "#ff6b6b", "#4ecdc4", "#ffe66d", "#a8e6cf",
    "#c7ceea", "#ff8b94", "#ffd93d", "#6c5ce7",
    "#fd79a8", "#00cec9", "#e17055", "#74b9ff",
]


@router.get(
    "/galaxy",
    summary="Merge multiple agent memory galaxies",
    description="Combine galaxy data from multiple agents into a single merged view. Stars are color-coded by source agent. Only agents with public vaults (or accessible to the caller) are included.",
)
async def federation_galaxy(
    peers: str = Query(..., description="Comma-separated agent names, max 10"),
    x_agent_key: Optional[str] = Header(None),
):
    peer_list = [p.strip() for p in peers.split(",") if p.strip()][:10]
    accessor_id = await _resolve_accessor(x_agent_key)

    all_stars, all_edges, all_nebulae = [], [], []
    included: list[str] = []

    for i, peer_name in enumerate(peer_list):
        try:
            async with get_db() as db:
                row = await (await db.execute(
                    "SELECT id, name FROM agents WHERE name=?", (peer_name,)
                )).fetchone()
            if not row:
                continue
            vault = MemoryVault(row[0], row[1])
            if not await vault.check_access(accessor_id, ""):
                continue
            data = vault.get_galaxy_data()
            color = _AGENT_COLORS[i % len(_AGENT_COLORS)]
            for star in data["stars"]:
                star["agent_name"] = peer_name
                star["agent_color"] = color
            all_stars.extend(data["stars"])
            all_edges.extend(data["edges"])
            all_nebulae.extend(data["nebulae"])
            included.append(peer_name)
        except Exception:
            continue

    return {
        "peers": peer_list,
        "included": included,
        "stars": all_stars,
        "edges": all_edges,
        "nebulae": all_nebulae,
        "clusters": {},
        "bounds": {"min": [0, 0, 0], "max": [8000, 1000, 500]},
    }
