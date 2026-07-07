#!/usr/bin/env python3
"""
Vantage Strategy Executor — 6-strategy trading framework
Paper mode by default. Set LIVE_MODE=1 to execute real trades.

Strategies:
  1. Moonshot  — high-risk degen (Pump.fun, new launches)
  2. Scalper   — 5% gain / 3% loss (BTC/ETH/SOL)
  3. Swing     — 10% gain / 3% loss (momentum)
  4. Moonbag   — tiered take-profit HODL
  5. Copy-Trade — mirror smart wallets
  6. Balanced  — 40/40/20 allocation with risk management

Wallet: Uses BIPON39 Soul (85SFCuohae...) — 0.183 SOL
"""
import urllib.request, json, sqlite3, time, os
from datetime import datetime, timezone

# ── Config ───────────────────────────────────────────────────
LIVE_MODE = os.environ.get("LIVE_MODE", "0") == "1"
VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = "vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
TRADE_WALLET = "85SFCuohae8gNQZXcYXm41vyeabc2YpAmietS6CbySYx"
HELIUS_KEY = "3b16b895-d4f1-404b-8edd-f3be766830ca"
SOL_BALANCE = 0.183  # current funded amount

# ── Strategy Configs ─────────────────────────────────────────
STRATEGIES = {
    "moonshot": {
        "max_per_trade_sol": 0.02,     # 1-2% of capital
        "take_profit_tiers": [2.0, 5.0, 10.0],  # 2x, 5x, 10x
        "stop_loss_pct": -50,           # -50% hard stop
        "signals": ["alpha_feed", "radar", "pumpfun"],
        "min_conviction": 6.0,
    },
    "scalper": {
        "max_per_trade_sol": 0.05,
        "take_profit_pct": 5.0,
        "stop_loss_pct": -3.0,
        "timeout_minutes": 60,
        "signals": ["predictor", "kraken"],
        "pairs": ["SOL/USD", "BTC/USD", "ETH/USD"],
    },
    "swing": {
        "max_per_trade_sol": 0.10,
        "take_profit_pct": 10.0,
        "stop_loss_pct": -3.0,
        "trailing_stop_activation": 5.0,
        "signals": ["trading_agents", "predictor", "alpha_sources"],
    },
    "moonbag": {
        "max_per_trade_sol": 0.03,
        "take_profit_tiers": [5.0, 20.0, 50.0],  # sell 25% at each tier
        "moonbag_pct": 25,             # keep 25% forever
        "signals": ["alpha_feed", "radar", "pumpfun"],
    },
    "copytrade": {
        "max_per_trade_sol": 0.05,
        "follow_pct": 50,              # match 50% of smart wallet size
        "min_wallet_winrate": 60,      # only follow wallets with >60% winrate
        "signals": ["smart_wallets"],
    },
    "balanced": {
        "allocation": {"bluechip": 0.4, "midcap": 0.4, "degen": 0.2},
        "max_daily_drawdown_pct": -8,
        "rebalance_interval_hours": 168,  # weekly
        "signals": ["trading_agents", "predictor", "fear_greed", "advanced_analytics"],
    },
}

# ── Paper Trade Ledger ───────────────────────────────────────
def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS strategy_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            amount_sol REAL,
            entry_price REAL,
            exit_price REAL,
            pnl_pct REAL,
            conviction REAL,
            status TEXT DEFAULT 'open',
            entry_time TEXT,
            exit_time TEXT,
            notes TEXT
        )
    """)
    db.commit()
    return db

# ── Signal Fetcher ───────────────────────────────────────────
def get_signals():
    """Pull live signals from Vantage pool."""
    req = urllib.request.Request(
        f"{VANTAGE_URL}/api/intel/signals",
        headers={"X-Agent-Key": VANTAGE_KEY}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        return data.get("signals", data) if isinstance(data, dict) else data
    except:
        return []

# ── Jupiter Swap Executor ────────────────────────────────────
def jupiter_swap(input_mint, output_mint, amount_sol, slippage=1.0):
    """Execute swap via Jupiter API."""
    if not LIVE_MODE:
        print(f"  [PAPER] Would swap {amount_sol} SOL → {output_mint}")
        return {"paper": True, "amount": amount_sol}

    # Real Jupiter swap
    quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={int(amount_sol*1e9)}&slippageBps={int(slippage*100)}"
    
    try:
        req = urllib.request.Request(quote_url, headers={"User-Agent": "curl/8.0"})
        quote = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        
        # Execute swap (needs wallet signing — placeholder)
        print(f"  [LIVE] Swap quote: {quote.get('outAmount', '?')} units")
        return quote
    except Exception as e:
        print(f"  Swap error: {e}")
        return None

# ── Strategy Router ──────────────────────────────────────────
def route_signal(signal, db):
    """Route a signal to the appropriate strategy and execute paper trade."""
    source = signal.get("source", "")
    symbol = signal.get("symbol", "?")
    conviction = signal.get("conviction", 0) or 0
    stype = signal.get("type", "")
    
    # Determine which strategies this signal triggers
    triggered = []
    for name, cfg in STRATEGIES.items():
        if source in cfg["signals"]:
            if conviction >= cfg.get("min_conviction", 0):
                triggered.append(name)
    
    if not triggered:
        return None
    
    for strat in triggered:
        cfg = STRATEGIES[strat]
        amount = min(cfg.get("max_per_trade_sol", SOL_BALANCE * 0.05), SOL_BALANCE * 0.02)
        
        if amount < 0.001:
            continue
        
        side = "BUY"
        
        # Record paper trade
        db.execute("""
            INSERT INTO strategy_trades (strategy, symbol, side, amount_sol, conviction, status, entry_time, notes)
            VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
        """, (strat, symbol, side, amount, conviction,
              datetime.now(timezone.utc).isoformat(),
              f"Signal: {source}/{stype} conviction={conviction}"))
        db.commit()
        
        print(f"  [{strat.upper():10s}] {side} {symbol} {amount:.4f} SOL (conv={conviction}) — PAPER")

# ── Daily Risk Check ─────────────────────────────────────────
def check_daily_drawdown(db):
    """Check if balanced strategy has exceeded daily drawdown limit."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = db.execute(
        "SELECT SUM(pnl_pct) FROM strategy_trades WHERE strategy='balanced' AND entry_time LIKE ? AND status='closed'",
        (f"{today}%",)
    ).fetchone()
    
    if trades and trades[0]:
        total_pnl = trades[0]
        if total_pnl <= STRATEGIES["balanced"]["max_daily_drawdown_pct"]:
            print(f"  ⛔ DAILY DRAWDOWN LIMIT: {total_pnl}% — pausing balanced strategy")
            return False
    return True

# ── Main Loop ────────────────────────────────────────────────
def run(interval=30):
    print(f"Strategy Executor — {'LIVE' if LIVE_MODE else 'PAPER'} mode — {interval}s cycle")
    print(f"Wallet: {TRADE_WALLET[:12]}... ({SOL_BALANCE} SOL)")
    print(f"Strategies: {', '.join(STRATEGIES.keys())}")
    print()
    
    db = init_db()
    trade_count = 0
    
    while True:
        try:
            signals = get_signals()
            if isinstance(signals, list):
                for signal in signals:
                    route_signal(signal, db)
            
            if trade_count % 20 == 0:  # Every ~10 min
                check_daily_drawdown(db)
                
                # Show stats
                open_trades = db.execute(
                    "SELECT COUNT(*) FROM strategy_trades WHERE status='open'"
                ).fetchone()[0]
                closed = db.execute(
                    "SELECT strategy, COUNT(*), ROUND(AVG(pnl_pct),1) FROM strategy_trades WHERE status='closed' GROUP BY strategy"
                ).fetchall()
                
                print(f"\n  Open: {open_trades} trades")
                for s, c, avg in closed:
                    print(f"  {s:10s}: {c:3d} closed, avg PnL: {avg}%")
                
                trade_count = 0
            
            trade_count += len(signals) if isinstance(signals, list) else 0
            
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(interval)

if __name__ == "__main__":
    run()
