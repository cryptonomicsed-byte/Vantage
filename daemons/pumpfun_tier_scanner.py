#!/opt/ares/venv/bin/python3
"""pumpfun_tier_scanner — real-time PRE-MIGRATION pump.fun bonding-curve
tracker, tiered by live USD market cap (5-10k / 10-20k / 20-30k / 30-40k /
40-50k / 50-60k).

Why this exists: degen_alpha_fusion.py and ogun_multiscan.py both say in
their own comments "this bot exists specifically to trade pre-migration
tokens" — but both source tokens from GeckoTerminal's trending_pools,
which can only ever return tokens that already have a real DEX pool, i.e.
already migrated off pump.fun's bonding curve. The intent was pre-migration
visibility; the actual data source structurally cannot deliver that. This
daemon is the piece that was missing: real-time bonding-curve state via
PumpPortal's free WebSocket (the same feed pumpfun_launch_radar.py already
uses for deployer-reputation flagging at creation), tracking every token's
live market cap as trades arrive, not just its creation moment.

Eviction (pump.fun moves fast — a stale entry is worse than no entry):
  - Below 10k USD mcap (fresh launch, may not have traded much yet): grace
    window of FRESH_GRACE_SECONDS (default 7 min) since last trade.
  - At or above 10k USD mcap (an already-running token): only
    RUNNING_GRACE_SECONDS (default 60s) since last trade — no trade in a
    minute on an active pump.fun token means it's dead.
  - A migration event drops a token immediately regardless of the above —
    it's no longer pre-migration by definition.

Quality filters (the "filter out the noise" ask): unique buyer/seller
diversity relative to trade count — a handful of wallets ping-ponging the
same SOL back and forth to fake volume shows up as a low unique-wallet-to-
trade-count ratio, and gets flagged rather than silently scored high on
raw volume alone.

Usage: pumpfun_tier_scanner.py
"""
import asyncio, json, os, sys, time
import sys as _vshim_sys
_vshim_sys.path.insert(0, "/opt/ares")
import vantage_db_shim as _vshim
import urllib.request
import websockets

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
FRESH_GRACE_SECONDS = int(os.environ.get("PUMPFUN_FRESH_GRACE_SECONDS", 7 * 60))
RUNNING_GRACE_SECONDS = int(os.environ.get("PUMPFUN_RUNNING_GRACE_SECONDS", 60))
FRESH_TIER_CEILING_USD = 10_000  # below this, use the longer grace window
EVICTION_SWEEP_SECONDS = 15
SOL_PRICE_REFRESH_SECONDS = 60
MAX_TRACKED_WALLETS_PER_TOKEN = 50  # cap unique_buyers/sellers list growth

# (floor_usd, ceiling_usd, tier_label) — 6 tiers, 5k to 60k
TIERS = [
    (5_000, 10_000, "5-10k"),
    (10_000, 20_000, "10-20k"),
    (20_000, 30_000, "20-30k"),
    (30_000, 40_000, "30-40k"),
    (40_000, 50_000, "40-50k"),
    (50_000, 60_000, "50-60k"),
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def classify_tier(market_cap_usd: float) -> str:
    for floor, ceiling, label in TIERS:
        if floor <= market_cap_usd < ceiling:
            return label
    if market_cap_usd < TIERS[0][0]:
        return ""  # not even in the lowest tier yet
    return "60k+"  # graduated out the top of the tracked range


async def fetch_sol_usd_price() -> float:
    """Vantage's own multi-source price endpoint first (already has its own
    fallback chain), then raw CoinGecko as a second-level fallback, then the
    last known cached value if both fail — never blocks the scanner on a
    transient network error."""
    try:
        req = urllib.request.Request(
            "http://localhost:8001/api/trading/markets/SOL/price",
            headers={"User-Agent": "Vantage/1.0"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        price = float(data.get("price", 0) or 0)
        if price > 0:
            return price
    except Exception as e:
        log(f"  Vantage price endpoint failed, trying CoinGecko: {e}")

    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            headers={"User-Agent": "Vantage/1.0"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
        price = float(data.get("solana", {}).get("usd", 0))
        return price if price > 0 else _sol_price_cache["value"]
    except Exception as e:
        log(f"  SOL price fetch failed, using cached ${_sol_price_cache['value']}: {e}")
        return _sol_price_cache["value"]


_sol_price_cache = {"value": 150.0, "updated_at": 0.0}  # seed default, refreshed on startup


def db_conn():
    conn = _vshim.get_sync_db()
    return conn


def upsert_new_token(conn, mint: str, symbol: str, name: str, deployer: str,
                      v_tokens: float, v_sol: float, market_cap_sol: float, sol_price: float) -> None:
    market_cap_usd = market_cap_sol * sol_price
    tier = classify_tier(market_cap_usd)
    conn.execute(
        """INSERT INTO pumpfun_premigration_tokens
             (mint, symbol, name, deployer, v_tokens_in_curve, v_sol_in_curve,
              market_cap_sol, market_cap_usd, tier, buy_count)
           VALUES (?,?,?,?,?,?,?,?,?,1)
           ON CONFLICT(mint) DO NOTHING""",
        (mint, symbol, name, deployer, v_tokens, v_sol, market_cap_sol, market_cap_usd, tier),
    )
    conn.commit()


def apply_trade(conn, mint: str, tx_type: str, trader: str,
                 v_tokens: float, v_sol: float, market_cap_sol: float, sol_price: float) -> None:
    row = conn.execute(
        "SELECT unique_buyers, unique_sellers, buy_count, sell_count FROM pumpfun_premigration_tokens WHERE mint=?",
        (mint,),
    ).fetchone()
    if row is None:
        return  # a trade for a mint we never saw create() for — ignore, can't score it fairly
    buyers = json.loads(row[0] or "[]")
    sellers = json.loads(row[1] or "[]")
    buy_count, sell_count = row[2] or 0, row[3] or 0

    if tx_type == "buy":
        buy_count += 1
        if trader not in buyers and len(buyers) < MAX_TRACKED_WALLETS_PER_TOKEN:
            buyers.append(trader)
    elif tx_type == "sell":
        sell_count += 1
        if trader not in sellers and len(sellers) < MAX_TRACKED_WALLETS_PER_TOKEN:
            sellers.append(trader)
    else:
        return

    market_cap_usd = market_cap_sol * sol_price
    tier = classify_tier(market_cap_usd)
    conn.execute(
        """UPDATE pumpfun_premigration_tokens SET
             v_tokens_in_curve=?, v_sol_in_curve=?, market_cap_sol=?, market_cap_usd=?,
             tier=?, buy_count=?, sell_count=?, unique_buyers=?, unique_sellers=?,
             volume_sol_total = volume_sol_total + ?, last_trade_at=datetime('now')
           WHERE mint=?""",
        (v_tokens, v_sol, market_cap_sol, market_cap_usd, tier, buy_count, sell_count,
         json.dumps(buyers), json.dumps(sellers), v_sol, mint),
    )
    conn.commit()


def mark_migrated(conn, mint: str) -> None:
    conn.execute("UPDATE pumpfun_premigration_tokens SET migrated=1 WHERE mint=?", (mint,))
    conn.commit()


def sweep_evictions(conn) -> int:
    """Two-speed eviction: fresh (<10k mcap) tokens get a long grace window
    since they may genuinely just not have traded in a while yet; anything
    that already cleared 10k gets evicted fast — pump.fun's own pace makes
    a 60s-stale "active" token almost certainly dead, not just quiet."""
    now = time.time()
    rows = conn.execute(
        "SELECT mint, market_cap_usd, last_trade_at FROM pumpfun_premigration_tokens "
        "WHERE evicted=0 AND migrated=0"
    ).fetchall()
    evicted = 0
    for mint, mcap_usd, last_trade_at in rows:
        try:
            last_ts = time.mktime(time.strptime(last_trade_at, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        age = now - last_ts
        grace = FRESH_GRACE_SECONDS if (mcap_usd or 0) < FRESH_TIER_CEILING_USD else RUNNING_GRACE_SECONDS
        if age > grace:
            conn.execute("UPDATE pumpfun_premigration_tokens SET evicted=1 WHERE mint=?", (mint,))
            evicted += 1
    if evicted:
        conn.commit()
    return evicted


def score_and_flag(conn) -> None:
    """Composite score per currently-tracked, non-evicted, non-migrated
    token: rewards real volume and diverse participation, penalizes the
    wash-trading pattern (few unique wallets accounting for a lot of
    trades). Score is only meaningful within a tier, not across tiers —
    a 6k-mcap token and a 55k-mcap token aren't competing for the same
    slot."""
    rows = conn.execute(
        "SELECT mint, buy_count, sell_count, unique_buyers, unique_sellers, "
        "volume_sol_total, created_at FROM pumpfun_premigration_tokens "
        "WHERE evicted=0 AND migrated=0"
    ).fetchall()
    now = time.time()
    for mint, buy_count, sell_count, ub_json, us_json, vol_sol, created_at in rows:
        buyers = json.loads(ub_json or "[]")
        sellers = json.loads(us_json or "[]")
        total_trades = (buy_count or 0) + (sell_count or 0)
        flags = []

        buyer_ratio = (len(buyers) / buy_count) if buy_count else 1.0
        seller_ratio = (len(sellers) / sell_count) if sell_count else 1.0
        if buy_count >= 10 and buyer_ratio < 0.25:
            flags.append("low_unique_buyer_diversity")
        if sell_count >= 10 and seller_ratio < 0.25:
            flags.append("low_unique_seller_diversity")

        try:
            created_ts = time.mktime(time.strptime(created_at, "%Y-%m-%d %H:%M:%S"))
            age_seconds = max(now - created_ts, 1)
        except Exception:
            age_seconds = 1
        trade_velocity = total_trades / age_seconds  # trades per second, a momentum proxy

        score = (
            min(vol_sol, 100) * 0.4          # real volume, capped so one whale trade can't dominate
            + min(total_trades, 50) * 0.3    # organic activity count
            + (buyer_ratio + seller_ratio) * 10  # participant diversity
            + min(trade_velocity * 100, 20)  # recent momentum
        )
        if flags:
            score *= 0.3  # heavy penalty, not a hard zero — still visible, clearly deprioritized

        conn.execute(
            "UPDATE pumpfun_premigration_tokens SET score=?, manipulation_flags=? WHERE mint=?",
            (round(score, 2), json.dumps(flags), mint),
        )
    conn.commit()


async def run() -> None:
    log("pumpfun_tier_scanner — real-time pre-migration bonding-curve tier tracker")
    conn = db_conn()
    _sol_price_cache["value"] = await fetch_sol_usd_price()
    _sol_price_cache["updated_at"] = time.time()
    log(f"  SOL/USD: ${_sol_price_cache['value']:.2f}")

    tracked_mints: set[str] = set()
    backoff = 5

    async def maintenance_loop(ws):
        while True:
            await asyncio.sleep(EVICTION_SWEEP_SECONDS)
            if time.time() - _sol_price_cache["updated_at"] > SOL_PRICE_REFRESH_SECONDS:
                _sol_price_cache["value"] = await fetch_sol_usd_price()
                _sol_price_cache["updated_at"] = time.time()
            n = sweep_evictions(conn)
            score_and_flag(conn)
            if n:
                log(f"  evicted {n} stale token(s)")

    while True:
        try:
            async with websockets.connect(PUMPPORTAL_WS, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log("subscribed to new-token + migration events")
                backoff = 5
                maint_task = asyncio.create_task(maintenance_loop(ws))
                try:
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        tx_type = event.get("txType", "")
                        mint = event.get("mint", "")
                        if not mint:
                            continue

                        if tx_type == "create":
                            symbol = event.get("symbol") or mint[:8]
                            name = event.get("name", "")
                            deployer = event.get("traderPublicKey", "")
                            v_tokens = float(event.get("vTokensInBondingCurve") or 0)
                            v_sol = float(event.get("vSolInBondingCurve") or 0)
                            mcap_sol = float(event.get("marketCapSol") or 0)
                            upsert_new_token(conn, mint, symbol, name, deployer, v_tokens, v_sol,
                                              mcap_sol, _sol_price_cache["value"])
                            if mint not in tracked_mints:
                                tracked_mints.add(mint)
                                await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))

                        elif tx_type in ("buy", "sell"):
                            trader = event.get("traderPublicKey", "")
                            v_tokens = float(event.get("vTokensInBondingCurve") or 0)
                            v_sol = float(event.get("vSolInBondingCurve") or 0)
                            mcap_sol = float(event.get("marketCapSol") or 0)
                            apply_trade(conn, mint, tx_type, trader, v_tokens, v_sol,
                                        mcap_sol, _sol_price_cache["value"])

                        elif "migrat" in tx_type.lower() or event.get("pool") == "migrated":
                            mark_migrated(conn, mint)
                            tracked_mints.discard(mint)
                finally:
                    maint_task.cancel()
        except Exception as e:
            log(f"WebSocket error: {e} — reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)


if __name__ == "__main__":
    asyncio.run(run())
