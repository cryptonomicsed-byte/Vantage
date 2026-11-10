"""The database-initialisation sequence, in one place.

`main.lifespan()` used to carry this list inline, which meant the only thing
that ever built the real schema was a running server. The test suite called
`init_agents_db()` alone, so every table created by a later init (channel
templates, mesh, receipts, tasks, live, degen indexes...) was absent under test
-- and the failures read as "no such table", which looks like broken code
rather than an incomplete fixture.

Keeping the sequence here means the app and the tests build the same schema, in
the same order, and a new init only has to be added once.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

__all__ = ["init_all_databases", "startup_db_call"]


async def startup_db_call(fn, *args, retries: int = 6, delay: float = 15.0, **kwargs):
    """Retry a DB-initialising coroutine when SQLite is temporarily locked.

    swarm_orchestrator and other daemons hold long write transactions against
    vantage.db; a 30-second busy_timeout is not always enough. This wrapper
    retries up to `retries` times with `delay`-second pauses so Vantage can
    start even while a bulk-write is in progress.
    """
    for attempt in range(retries):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            if "database is locked" in str(exc).lower() and attempt < retries - 1:
                logger.warning(
                    "Startup DB call %s locked (attempt %d/%d), retrying in %.0fs…",
                    fn.__name__, attempt + 1, retries, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise


async def init_all_databases() -> list[str]:
    """Run every DB init the app runs at boot. Returns the names that failed.

    A failure here is logged and collected rather than raised: boot has always
    been best-effort per subsystem (one locked table must not take the API
    down), and callers that care -- the test fixture -- can assert on the list.
    """
    from .agents import init_agents_db
    from .mesh_store import init_mesh_db
    from .manifesto_store import init_manifesto_db

    from .routers.copilot import init_copilot_db
    from .routers.pine import init_pine_db
    from .routers.genesis import _init_genesis_db
    from .routers.collectives import init_collectives_db
    from .coordination import init_coordination_db
    from .work_refs import init_work_ref_db
    from .workspace_roles import (
        deduplicate_builtin_templates, init_workspace_roles_db, seed_builtin_templates,
    )
    from .presence import init_presence_db
    from .space_provisioning import init_space_provisioning_db, backfill_space_channels
    from .receipts import init_receipts_db
    # Aliased: backend/mesh_store.py already exports init_mesh_db for the
    # Block Mesh coordination tables. A bare `from .mesh_gateway import
    # init_mesh_db` rebinds the name for the whole function and shadows the
    # import above, which an earlier version did at the cost of a boot crash.
    from .mesh_gateway import init_mesh_db as init_meshnet_db
    from .coordination_join import init_join_db
    from .routers.conductor import init_conductor_db
    from .coordination_scoring import init_scoring_db
    from .sovereignty import init_sovereignty_db
    from .intel_exchange import init_intel_exchange_db
    from .routers.wallets import init_wallet_tables
    from .routers.degen import ensure_degen_indexes
    from .db import init_workspace_tasks_db
    from .tasks_db import init_tasks_db
    from .routers.blockmesh_board import init_blockmesh_board_db
    from .device_registry import init_device_registry_db
    from .reputation import init_task_reputation_db
    from .tier_engine import init_tier_db
    from .witness_store import init_witness_db
    from .routers.live import init_live_db

    sequence = [
        init_agents_db,
        init_mesh_db,
        init_manifesto_db,
        init_copilot_db,
        init_pine_db,
        _init_genesis_db,
        init_collectives_db,
        init_coordination_db,
        init_work_ref_db,
        init_workspace_roles_db,
        deduplicate_builtin_templates,
        seed_builtin_templates,
        init_presence_db,
        init_space_provisioning_db,
        backfill_space_channels,
        init_receipts_db,
        init_meshnet_db,
        init_join_db,
        init_conductor_db,
        init_scoring_db,
        init_sovereignty_db,
        init_intel_exchange_db,
        init_wallet_tables,
        ensure_degen_indexes,
        init_workspace_tasks_db,
        init_tasks_db,
        init_blockmesh_board_db,
        init_device_registry_db,
        init_task_reputation_db,
        init_tier_db,
        init_witness_db,
        init_live_db,
    ]

    failed: list[str] = []
    for fn in sequence:
        try:
            await startup_db_call(fn)
        except Exception as exc:
            logger.warning("DB init %s failed: %s", getattr(fn, "__name__", fn), exc)
            failed.append(getattr(fn, "__name__", str(fn)))
    return failed
