#!/opt/ares/venv/bin/python3
"""degen_alpha_fusion — Combines Pump.fun launches + wallet activity + volume + moonshot scoring.
Auto-lists high-scoring tokens to pumpfun watchlist. Posts fusion signals.
"""
import time, json, sqlite3, os, sys, urllib.request, signal

DB = "/opt/ares/Vantage/data/vantage.db"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
BIRDEYE_KEY = os.environ.get("BIRDEYE_KEY", "")
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
VANTAGE_URL = "http://localhost:8001/api/trading/signals/ingest"

def fetch(url, headers=None):
    h = headers or {}; h['User-Agent'] = 'curl/8.0'
    req = urllib.request.Request(url, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def ingest(symbol, direction, conviction, detail):
    try:
        payload = json.dumps(dict(symbol=symbol, source="degen_alpha_fusion", direction=direction, conviction=min(1.0, conviction), type="degen_fusion", detail=detail[:500])).encode()
        urllib.request.urlopen(urllib.request.Request(VANTAGE_URL, data=payload, headers={"Content-Type":"application/json","X-Agent-Key":VANTAGE_KEY}), timeout=5)
        print(f"  ✅ {symbol} {direction} c={conviction:.2f}")
    except Exception as e:
        print(f"  ❌ Ingest fail: {e}")

def get_trending_pools():
    try:
        d = fetch("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1", {"accept":"application/json"})
        pools = d.get("data",[])
        results = []
        for p in pools[:20]:
            attrs = p.get("attributes",{})
            name = attrs.get("name","")
            addr = p.get("id","").split("_")[-1] if "_" in p.get("id","") else ""
            vol = attrs.get("volume_usd",{})
            vol_1h = float(str(vol.get("h1",0))) if isinstance(vol,dict) else 0
            vol_24h = float(str(vol.get("h24",0))) if isinstance(vol,dict) else 0
            pc = attrs.get("price_change_percentage",{})
            pc_1h = float(str(pc.get("h1",0))) if isinstance(pc,dict) else 0
            txns = attrs.get("transactions",{})
            buys_1h = txns.get("h1",{}).get("buys",0) if isinstance(txns.get("h1"),dict) else 0
            results.append({"name":name,"address":addr,"vol_1h":vol_1h,"vol_24h":vol_24h,"pc_1h":pc_1h,"buys_1h":buys_1h})
        return results
    except:
        return []

def rug_check(mint):
    try:
        import urllib.request as _ur
        payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[mint,{"encoding":"jsonParsed"}]}).encode()
        req = _ur.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}", data=payload, headers={"Content-Type":"application/json"})
        resp = _ur.urlopen(req, timeout=15).read().decode()
        result = json.loads(resp)
        value = result.get("result", {}).get("value", {})
        if not value: return mint, 0, False
        parsed = value.get("data", {}).get("parsed", {})
        info = parsed.get("info", {}) if parsed else {}
        mint_auth = bool(info.get("mintAuthority"))
        freeze_auth = bool(info.get("freezeAuthority"))
        risk = (40 if mint_auth else 0) + (30 if freeze_auth else 0)
        return mint, risk, risk >= 40
    except:
        return mint, 0, False

def score_moonshot(pool):
    score = 0
    vol_24h = pool.get("vol_24h", 0) or 0
    buys_1h = pool.get("buys_1h", 0) or 0
    pc_1h = pool.get("pc_1h", 0) or 0
    if vol_24h > 50000: score += 20
    elif vol_24h > 10000: score += 10
    if buys_1h > 200: score += 25
    elif buys_1h > 50: score += 15
    if pc_1h > 10: score += 20
    elif pc_1h > 5: score += 10
    return min(100, max(0, score))

def add_to_pumpfun_watchlist(mint, name):
    try:
        url = f"http://localhost:8001/api/intel/pumpfun/watchlist?mint={mint}&label={name[:20]}"
        req = urllib.request.Request(url, headers={"X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
        print(f"  📦 Auto-listed: {name[:20]}")
    except:
        pass

def snipe_token(symbol, moonshot):
    """Auto-buy high-confidence moonshots (0.01 SOL) via trading API."""
    try:
        payload = json.dumps({
            "symbol": f"{symbol}/USDC",
            "side": "buy",
            "chain": "solana",
            "quantity": 0.01,
            "order_type": "market",
            "trigger_reason": f"moonshot snipe — score={moonshot}",
        }).encode()
        req = urllib.request.Request(
            "http://localhost:8001/api/trading/orders",
            data=payload,
            headers={"Content-Type":"application/json", "X-Agent-Key": VANTAGE_KEY}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        order_id = resp.get("id", resp.get("order_id", "?"))
        print(f"  🎯 SNIPED: {symbol} — order #{order_id}")
        return order_id
    except Exception as e:
        print(f"  ❌ Snipe failed: {e}")
        return None

def run():
    print("═══ degen_alpha_fusion v2 (moonshot) ═══")
    checked = set()
    while True:
        try:
            pools = get_trending_pools()
            print(f"\n  [{time.strftime('%H:%M:%S')}] {len(pools)} trending pools")
            for p in pools:
                addr = p.get("address","")
                if not addr or addr in checked: continue
                sym = p["name"].split(" / ")[0][:10] if " / " in p["name"] else p["name"][:10]
                vol_1h = p.get("vol_1h",0) or 0
                buys_1h = p.get("buys_1h",0) or 0
                pc_1h = p.get("pc_1h",0) or 0

                conviction = 0.3
                moonshot = score_moonshot(p)
                if moonshot > conviction * 100: conviction = moonshot / 100

                if vol_1h > 10000: conviction += 0.2
                if vol_1h > 50000: conviction += 0.1
                if buys_1h > 100: conviction += 0.2
                if pc_1h > 10: conviction += 0.15
                if pc_1h > 50: conviction += 0.15

                if conviction >= 0.5 and addr:
                    _, risk, is_rug = rug_check(addr)
                    if is_rug:
                        print(f"  🚫 {sym}: rug risk - skip")
                        checked.add(addr); continue

                if conviction >= 0.5:
                    direction = "BUY" if pc_1h > -5 else "SELL"
                    detail = f"Fusion: vol=${vol_1h:.0f} buys_1h={buys_1h} pc_1h={pc_1h:.1f}% | moonshot={moonshot}"
                    if moonshot > 50: add_to_pumpfun_watchlist(addr, sym)
                    if moonshot > 60: snipe_token(sym, moonshot)  # 🎯 auto-snipe at 60+
                    print(f"  🔥 {sym}: {direction} c={conviction:.2f} v=${vol_1h:.0f} ms={moonshot}")
                    ingest(sym, direction, conviction, detail)
                checked.add(addr)
            if len(checked) > 5000: checked.clear()
            time.sleep(120)
        except Exception as e:
            print(f"  ⚠️ Loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
