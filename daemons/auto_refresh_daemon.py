#!/usr/bin/env python3
"""
Auto-refresh daemon -- three schedules:
  6h: KOL link refresh
  2h: mint backtrack
  12h: wallet cluster rebuild
"""
import asyncio, time, sys, os
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/vantage.db")

INTERVAL_KOL_LINK        = int(os.environ.get("REFRESH_KOL_LINK_SECS",       str(6  * 3600)))
INTERVAL_MINT_BACKTRACK  = int(os.environ.get("REFRESH_MINT_BACKTRACK_SECS",  str(2  * 3600)))
INTERVAL_CLUSTER_REBUILD = int(os.environ.get("REFRESH_CLUSTER_REBUILD_SECS", str(12 * 3600)))
TICK = int(os.environ.get("REFRESH_TICK_SECS", "60"))

def _auth_headers():
    """The alpha routes are guarded by get_agent, not by the tool key.
    Sending X-Vantage-Tool-Key returned 401 on every rebuild, which is
    why wallet_clusters never filled."""
    key = (os.environ.get("VANTAGE_AGENT_KEY")
           or os.environ.get("VANTAGE_TOOL_KEY", ""))
    return {"X-Agent-Key": key} if key else {}


LAST_RUN = {"kol_link": 0, "mint_backtrack": 0, "cluster_rebuild": 0}


async def refresh_kol_links():
    print("[auto_refresh] KOL link refresh starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("http://localhost:8001/api/alpha/social-link/refresh",
                                  headers=_auth_headers())
            print(f"[auto_refresh] KOL link: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] KOL link error (fail-open): {e}", flush=True)


async def refresh_mint_backtrack():
    """Backtrack early buyers for recent tokens.

    The route requires a `mint`; calling it bare returned 422 forever. Source
    candidates from wallet_trades and skip cleanly when there are none.
    """
    print("[auto_refresh] Mint backtrack refresh starting", flush=True)
    try:
        import httpx
        from backend.db import get_db

        async with get_db() as db:
            cur = await db.execute(
                "SELECT token_mint FROM wallet_trades "
                "WHERE token_mint IS NOT NULL AND token_mint != '' "
                "GROUP BY token_mint ORDER BY MAX(timestamp) DESC LIMIT 3")
            mints = [r[0] for r in await cur.fetchall()]

        if not mints:
            print("[auto_refresh] Mint backtrack: no mints queued, skipping", flush=True)
            return

        async with httpx.AsyncClient(timeout=120) as client:
            for mint in mints:
                r = await client.post(
                    "http://localhost:8001/api/alpha/backtrack",
                    json={"mint": mint},
                    headers=_auth_headers())
                if r.status_code >= 400:
                    print(f"[auto_refresh] ERROR Mint backtrack {mint[:8]} HTTP {r.status_code}",
                          flush=True)
                else:
                    print(f"[auto_refresh] Mint backtrack {mint[:8]}: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] Backtrack error (fail-open): {e}", flush=True)


async def rebuild_wallet_clusters():
    print("[auto_refresh] Wallet cluster rebuild starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post("http://localhost:8001/api/alpha/clusters/rebuild",
                                  headers=_auth_headers())
            if r.status_code >= 400:
                print(f"[auto_refresh] ERROR Cluster rebuild HTTP {r.status_code}", flush=True)
            else:
                print(f"[auto_refresh] Cluster rebuild: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] Cluster rebuild error (fail-open): {e}", flush=True)


async def main():
    print(
        f"[auto_refresh] Starting auto-refresh daemon "
        f"(tick={TICK}s kol={INTERVAL_KOL_LINK}s backtrack={INTERVAL_MINT_BACKTRACK}s cluster={INTERVAL_CLUSTER_REBUILD}s)",
        flush=True
    )
    while True:
        now = time.time()
        if now - LAST_RUN["kol_link"] >= INTERVAL_KOL_LINK:
            await refresh_kol_links()
            LAST_RUN["kol_link"] = now
        if now - LAST_RUN["mint_backtrack"] >= INTERVAL_MINT_BACKTRACK:
            await refresh_mint_backtrack()
            LAST_RUN["mint_backtrack"] = now
        if now - LAST_RUN["cluster_rebuild"] >= INTERVAL_CLUSTER_REBUILD:
            await rebuild_wallet_clusters()
            LAST_RUN["cluster_rebuild"] = now
        await asyncio.sleep(TICK)


if __name__ == "__main__":
    asyncio.run(main())
