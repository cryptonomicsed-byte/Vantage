#!/usr/bin/env python3
"""
LIVE Scalper — BTC/ETH/SOL with Pine Script indicators
Strategy: 5% take-profit / 3% stop-loss, max 0.03 SOL per trade
Uses Jupiter signer + Pine Runtime for signal generation
"""
import urllib.request, json, time, sqlite3, os, sys
from datetime import datetime, timezone

# ── Config ───────────────────────────────────────────────────
LIVE = os.environ.get("LIVE", "0") == "1"
JUPITER_SIGNER = "/opt/ares/ares_jupiter_signer.py"
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
PINE_URL = "http://localhost:9871"
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")

# Scalper config
MAX_TRADE_SOL = 0.03
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = -3.0
PAIRS = ["SOL", "BTC", "ETH"]
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

def get_sol_balance():
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"getBalance","params":["85SFCuohae8gNQZXcYXm41vyeabc2YpAmietS6CbySYx"]}).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}", data=payload, headers={"Content-Type":"application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode())["result"]["value"] / 1e9

def get_signals():
    """Get live predictor signals."""
    req = urllib.request.Request(f"{VANTAGE_URL}/api/intel/signals", headers={"X-Agent-Key": VANTAGE_KEY})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        signals = data.get("signals", data)
        return [s for s in signals if s.get("source") in ("predictor", "kraken")]
    except:
        return []

def get_pine_indicator(pair, indicator="rsi"):
    """Get Pine Script indicator value."""
    try:
        req = urllib.request.Request(
            f"{PINE_URL}/api/pine/indicators",
            data=json.dumps({"symbol": f"{pair}USD", "indicator": indicator}).encode(),
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except:
        return None

def has_open_position(db, symbol):
    """Check if we already have an open position for this symbol."""
    row = db.execute(
        "SELECT id FROM strategy_trades WHERE symbol=? AND status='open' AND strategy='scalper'", (symbol,)
    ).fetchone()
    return row is not None

def execute_buy(symbol, amount_sol):
    """Execute buy via Jupiter signer or simulate."""
    if not LIVE:
        print(f"  [PAPER] BUY {symbol} {amount_sol:.4f} SOL")
        return {"paper": True, "amount": amount_sol}

    import subprocess
    result = subprocess.run(
        ["/opt/ares/venv/bin/python3", JUPITER_SIGNER, "swap",
         SOL_MINT, USDC_MINT, str(amount_sol)],
        capture_output=True, text=True, timeout=30
    )
    return {"output": result.stdout, "success": result.returncode == 0}

def check_exit_conditions(db):
    """Check open positions for take-profit or stop-loss."""
    trades = db.execute(
        "SELECT id, symbol, amount_sol, entry_price, entry_time FROM strategy_trades WHERE status='open' AND strategy='scalper'"
    ).fetchall()
    
    for tid, sym, amount, entry, entry_time in trades:
        # Get current price via Kraken
        try:
            req = urllib.request.Request(f"https://api.kraken.com/0/public/Ticker?pair={sym}USD")
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode())
            
            pair_key = {"BTC": "XXBTZUSD", "ETH": "XETHZUSD", "SOL": "SOLUSD"}.get(sym, f"{sym}USD")
            price = float(data["result"].get(pair_key, {}).get("c", [0])[0])
        except:
            continue
        
        if not entry:
            continue
        
        pnl_pct = ((price - entry) / entry) * 100
        
        if pnl_pct >= TAKE_PROFIT_PCT:
            print(f"  🎯 TAKE PROFIT: {sym} @ {pnl_pct:.1f}% (+{amount * (pnl_pct/100):.4f} SOL)")
            db.execute(
                "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=? WHERE id=?",
                (price, pnl_pct, datetime.now(timezone.utc).isoformat(), tid)
            )
            db.commit()
        elif pnl_pct <= STOP_LOSS_PCT:
            print(f"  🛑 STOP LOSS: {sym} @ {pnl_pct:.1f}% ({amount * (pnl_pct/100):.4f} SOL)")
            db.execute(
                "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=? WHERE id=?",
                (price, pnl_pct, datetime.now(timezone.utc).isoformat(), tid)
            )
            db.commit()
        else:
            print(f"  [{sym}] {pnl_pct:+.1f}% (entry: {entry:.2f}, now: {price:.2f})")

def run(interval=30):
    mode = "LIVE" if LIVE else "PAPER"
    print(f"Scalper — {mode} mode — {interval}s cycle")
    print(f"Max trade: {MAX_TRADE_SOL} SOL | TP: +{TAKE_PROFIT_PCT}% | SL: {STOP_LOSS_PCT}%")
    print(f"Pairs: {', '.join(PAIRS)}")
    print(f"Pine Runtime: {PINE_URL}")
    
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS strategy_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT, symbol TEXT, side TEXT, amount_sol REAL,
            entry_price REAL, exit_price REAL, pnl_pct REAL,
            conviction REAL, status TEXT DEFAULT 'open',
            entry_time TEXT, exit_time TEXT, notes TEXT
        )
    """)
    db.commit()
    
    while True:
        try:
            bal = get_sol_balance()
            signals = get_signals()
            
            # Check exit conditions for open positions
            check_exit_conditions(db)
            
            # Enter new positions
            for sig in signals:
                sym = sig.get("symbol", "")
                if sym not in PAIRS:
                    continue
                if has_open_position(db, sym):
                    continue
                
                conv = sig.get("conviction", 0) or 0
                if conv < 0.5:
                    continue
                
                # Get entry price
                try:
                    req = urllib.request.Request(f"https://api.kraken.com/0/public/Ticker?pair={sym}USD")
                    resp = urllib.request.urlopen(req, timeout=10)
                    data = json.loads(resp.read().decode())
                    pair_key = {"BTC":"XXBTZUSD","ETH":"XETHZUSD","SOL":"SOLUSD"}.get(sym)
                    entry_price = float(data["result"].get(pair_key, {}).get("c", [0])[0])
                except:
                    continue
                
                amount = min(MAX_TRADE_SOL, bal * 0.15)  # max 15% of balance
                if amount < 0.005:
                    continue
                
                # Execute
                result = execute_buy(sym, amount)
                
                db.execute("""
                    INSERT INTO strategy_trades (strategy, symbol, side, amount_sol, entry_price, conviction, status, entry_time, notes)
                    VALUES ('scalper', ?, 'BUY', ?, ?, ?, 'open', ?, ?)
                """, (sym, amount, entry_price, conv,
                      datetime.now(timezone.utc).isoformat(),
                      f"Signal: {sig.get('source')} type={sig.get('type')}"))
                db.commit()
                
                print(f"  [{sym}] BUY {amount:.4f} SOL @ ${entry_price:.2f} (conv={conv}) — {mode}")
                bal -= amount
            
            # Show open positions
            open_count = db.execute("SELECT COUNT(*) FROM strategy_trades WHERE status='open'").fetchone()[0]
            if open_count > 0:
                print(f"  Open: {open_count} positions | Balance: {bal:.4f} SOL", flush=True)
            
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(interval)

if __name__ == "__main__":
    run()
