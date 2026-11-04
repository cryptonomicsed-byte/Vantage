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

LAST_RUN = {"kol_link": 0, "mint_backtrack": 0, "cluster_rebuild": 0}


async def refresh_kol_links():
    print("[auto_refresh] KOL link refresh starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("http://localhost:8001/api/alpha/social-link/refresh",
                                  headers={"X-Vantage-Tool-Key": os.environ.get("VANTAGE_TOOL_KEY", "")})
            print(f"[auto_refresh] KOL link: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] KOL link error (fail-open): {e}", flush=True)


async def refresh_mint_backtrack():
    print("[auto_refresh] Mint backtrack refresh starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post("http://localhost:8001/api/alpha/backtrack",
                                  headers={"X-Vantage-Tool-Key": os.environ.get("VANTAGE_TOOL_KEY", "")})
            print(f"[auto_refresh] Mint backtrack: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] Backtrack error (fail-open): {e}", flush=True)


async def rebuild_wallet_clusters():
    print("[auto_refresh] Wallet cluster rebuild starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post("http://localhost:8001/api/alpha/clusters/rebuild",
                                  headers={"X-Vantage-Tool-Key": os.environ.get("VANTAGE_TOOL_KEY", "")})
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
