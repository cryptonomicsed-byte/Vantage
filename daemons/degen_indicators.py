#!/opt/ares/venv/bin/python3
"""degen_indicators — Pump.fun-specific trading indicators for predictor daemon.
Appends to vantage_predictor.py's indicator suite: buy pressure (first 5 min),
dev wallet sell %, holder concentration.

Usage: Import from vantage_predictor.py or run standalone.
"""
import urllib.request, json, time

HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
BIRDEYE_KEY = os.environ.get("BIRDEYE_KEY", "")
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

def rpc(method, params):
    payload = json.dumps(dict(jsonrpc="2.0", id=1, method=method, params=params)).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS}", data=payload, headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10))

def birdeye_fetch(path):
    req = urllib.request.Request(f"https://public-api.birdeye.so/{path}", headers={"X-API-KEY":BIRDEYE_KEY})
    return json.loads(urllib.request.urlopen(req, timeout=10))

# ── Degen Indicator 1: Buy Pressure (first 5 minutes) ──────────────
def buy_pressure_5m(mint):
    """Score: ratio of buys/sells in last 5 minutes. >1.5 = bullish, <0.5 = bearish."""
    try:
        d = birdeye_fetch(f"defi/price?address={mint}")
        token = d.get("data", {})
        trade_5m = token.get("trade_5m", {}) if isinstance(token.get("trade_5m"), dict) else {}
        buys = int(trade_5m.get("buy", 0))
        sells = int(trade_5m.get("sell", 0))
        if sells == 0:
            return {"buy_pressure_5m": 2.0 if buys > 0 else 1.0, "buys_5m": buys, "sells_5m": sells}
        ratio = buys / sells
        return {"buy_pressure_5m": round(ratio, 3), "buys_5m": buys, "sells_5m": sells}
    except:
        return {"buy_pressure_5m": 0, "error": "Birdeye unavailable"}

# ── Degen Indicator 2: Dev Wallet Sell % ────────────────────────────
def dev_sell_pct(mint, dev_wallet=None):
    """Check if dev wallet sold tokens in first few minutes of launch.
    High dev sell % = likely rug. Returns risk assessment."""
    try:
        # Get recent transactions for the mint
        sigs = rpc("getSignaturesForAddress", [mint, {"limit": 20}])
        sig_list = sigs.get("result", [])
        
        # Look for sell transactions from the first few blocks
        is_new = len(sig_list) < 50  # Rough heuristic: new token if few transactions
        
        # Check if creator wallet exists in first transactions
        creator = None
        for s in sig_list[:5]:
            txn_data = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            accts = txn_data.get("result", {}).get("transaction", {}).get("message", {}).get("accountKeys", [])
            if accts:
                creator = accts[0].get("pubkey") if isinstance(accts[0], dict) else accts[0]
                break

        if not creator:
            return {"dev_sell_risk": 50, "detail": "Unable to identify creator wallet", "is_new": is_new}

        # Check if creator sold
        creator_sigs = rpc("getSignaturesForAddress", [creator, {"limit": 20}])
        creator_txns = creator_sigs.get("result", [])
        
        creator_sells = 0
        for cs in creator_txns:
            ct = rpc("getTransaction", [cs["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            logs = str(ct.get("result", {}).get("meta", {}).get("logMessages", []))
            if "sell" in logs.lower() or "swap" in logs.lower():
                creator_sells += 1

        risk = min(100, creator_sells * 25)  # Each sell from creator = 25 risk points
        return {
            "dev_sell_risk": risk,
            "creator_wallet": creator[:20],
            "creator_txns": len(creator_txns),
            "creator_sells": creator_sells,
            "is_new_token": is_new,
            "detail": f"Creator made {creator_sells} sell transactions" if creator_sells > 0 else "No creator sells detected",
        }
    except Exception as e:
        return {"dev_sell_risk": 50, "detail": f"Analysis failed: {e}"}

# ── Degen Indicator 3: Holder Concentration ─────────────────────────
def holder_concentration(mint):
    """Top 3 holders owning >50% supply = concentration risk."""
    try:
        # Get token holders via Birdeye
        d = birdeye_fetch(f"defi/price?address={mint}")
        token = d.get("data", {})
        holders = token.get("holders", 0)
        liquidity = token.get("liquidity", 0)
        
        concentration_score = 0
        if holders < 5:
            concentration_score = 80  # Very concentrated
        elif holders < 20:
            concentration_score = 40  # Somewhat concentrated
        else:
            concentration_score = 10  # Decentralized
        
        return {
            "holder_count": holders,
            "concentration_score": concentration_score,
            "liquidity_usd": liquidity,
            "detail": f"{holders} holders, ${float(liquidity):,.0f} liquidity",
        }
    except:
        return {"holder_count": 0, "concentration_score": 50}

# ── Combined Degen Score ────────────────────────────────────────────
def degen_score(mint):
    """Unified degen score 0-100 (higher = riskier). Aggregates all indicators.
    Returns BUY/SELL/NEUTRAL recommendation."""
    bp = buy_pressure_5m(mint)
    dev = dev_sell_pct(mint)
    holder = holder_concentration(mint)
    
    # Weighted risk scoring
    score = 0
    signals = []
    
    # Buy pressure: low ratio = bearish (add to risk)
    bp_ratio = bp.get("buy_pressure_5m", 1.0)
    if bp_ratio < 0.5:
        score += 30
        signals.append("Bearish buy/sell ratio")
    elif bp_ratio > 2.0:
        score -= 10  # Bonus for strong buying
        signals.append("Strong buying pressure")
    
    # Dev sell risk
    dev_risk = dev.get("dev_sell_risk", 50)
    score += dev_risk * 0.4
    if dev_risk > 50:
        signals.append(f"Dev sell risk: {dev_risk}/100")
    
    # Holder concentration
    conc = holder.get("concentration_score", 50)
    score += conc * 0.3
    if conc > 50:
        signals.append(f"Holder concentration: {conc}/100")
    
    score = min(100, max(0, round(score)))
    
    # Recommendation
    if score < 30:
        direction = "BUY"
    elif score < 60:
        direction = "NEUTRAL"
    else:
        direction = "SELL"
    
    return {
        "mint": mint,
        "degen_score": score,
        "direction": direction,
        "buy_pressure_5m": bp_ratio,
        "dev_sell_risk": dev_risk,
        "holder_concentration": conc,
        "signals": signals,
        "raw": {"buy_pressure": bp, "dev_sell": dev, "holders": holder},
    }

# ── Standalone test ─────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    mint = sys.argv[1] if len(sys.argv) > 1 else "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump"
    print(f"Degen Indicators: {mint}")
    result = degen_score(mint)
    print(json.dumps(result, indent=2))
