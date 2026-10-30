#!/usr/bin/env python3
"""Auto-refresh daemon: KOL links (6h), mint backtrack (2h), wallet cluster rebuild (12h)."""
import asyncio, time, sys, os
sys.path.insert(0, "/opt/ares/Vantage")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////opt/ares/Vantage/vantage.db")

LAST_RUN = {"kol_link": 0, "mint_backtrack": 0, "cluster_rebuild": 0}
INTERVALS = {"kol_link": 6 * 3600, "mint_backtrack": 2 * 3600, "cluster_rebuild": 12 * 3600}

async def refresh_kol_links():
    print("[auto_refresh] KOL link refresh starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("http://localhost:8001/api/alpha/social-link/refresh",
                                  headers={"X-Vantage-Tool-Key": os.environ.get("VANTAGE_TOOL_KEY", "")})
            print(f"[auto_refresh] KOL link: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] KOL link error: {e}", flush=True)

async def refresh_mint_backtrack():
    print("[auto_refresh] Mint backtrack refresh starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post("http://localhost:8001/api/alpha/backtrack",
                                  headers={"X-Vantage-Tool-Key": os.environ.get("VANTAGE_TOOL_KEY", "")})
            print(f"[auto_refresh] Mint backtrack: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] Backtrack error: {e}", flush=True)

async def rebuild_wallet_clusters():
    print("[auto_refresh] Wallet cluster rebuild starting", flush=True)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post("http://localhost:8001/api/alpha/clusters/rebuild",
                                  headers={"X-Vantage-Tool-Key": os.environ.get("VANTAGE_TOOL_KEY", "")})
            print(f"[auto_refresh] Cluster rebuild: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[auto_refresh] Cluster rebuild error: {e}", flush=True)

async def main():
    print("[auto_refresh] Starting auto-refresh daemon", flush=True)
    while True:
        now = time.time()
        if now - LAST_RUN["kol_link"] >= INTERVALS["kol_link"]:
            await refresh_kol_links()
            LAST_RUN["kol_link"] = now
        if now - LAST_RUN["mint_backtrack"] >= INTERVALS["mint_backtrack"]:
            await refresh_mint_backtrack()
            LAST_RUN["mint_backtrack"] = now
        if now - LAST_RUN["cluster_rebuild"] >= INTERVALS["cluster_rebuild"]:
            await rebuild_wallet_clusters()
            LAST_RUN["cluster_rebuild"] = now
        await asyncio.sleep(300)  # check every 5 minutes

if __name__ == "__main__":
    asyncio.run(main())
