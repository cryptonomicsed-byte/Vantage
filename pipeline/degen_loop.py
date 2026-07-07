#!/usr/bin/env python3
"""
DEGEN LOOP — Continuous Pump.fun scalping with profit-splitting
Strategy: 50% profit → vault (USDC) | 50% → compound into next trade
Start: $7 (~0.085 SOL) per trade
"""
import subprocess, json, sqlite3, time, os, urllib.request
from datetime import datetime, timezone

VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = "vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
WALLET = "ogun"

# ── Degen Loop Config ────────────────────────────────────────
INITIAL_TRADE_SOL = 0.085   # ~$7
VAULT_PCT = 0.50            # 50% profit → vault
COMPOUND_PCT = 0.50         # 50% profit → compound
TAKE_PROFIT = 0.25          # Exit at +25%
STOP_LOSS = -0.30           # Exit at -30%
MIN_CONVICTION = 0.85       # Only trade highest conviction

TRADED = set()
TRADE_COUNT = 0
CURRENT_BANKROLL = INITIAL_TRADE_SOL

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS degen_vault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount_sol REAL,
            action TEXT,
            tx_sig TEXT,
            created_at TEXT
        )
    """)
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
    return db

def vault_deposit(amount_sol, tx_sig=""):
    """Deposit profit into vault (stake as USDC)."""
    db = sqlite3.connect(DB_PATH)
    db.execute(
        "INSERT INTO degen_vault (amount_sol, action, tx_sig, created_at) VALUES (?, 'deposit', ?, ?)",
        (amount_sol, tx_sig, datetime.now(timezone.utc).isoformat())
    )
    db.commit()
    db.close()
    print(f"  🏦 VAULT: +{amount_sol:.4f} SOL vaulted (total: {get_vault_total():.4f} SOL)")

def get_vault_total():
    db = sqlite3.connect(DB_PATH)
    total = db.execute("SELECT COALESCE(SUM(amount_sol), 0) FROM degen_vault WHERE action='deposit'").fetchone()[0]
    withdrawals = db.execute("SELECT COALESCE(SUM(amount_sol), 0) FROM degen_vault WHERE action='withdraw'").fetchone()[0]
    db.close()
    return total - withdrawals

def get_token_price(symbol):
    try:
        result = subprocess.run(["sol", "token", "price", symbol.lower()],
                               capture_output=True, text=True, timeout=15)
        for line in result.stdout.split("\n"):
            if "$" in line and symbol.lower() in line.lower():
                return float(line.split("$")[-1].strip())
    except: pass
    return None

def get_degen_signals():
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals",
            headers={"X-Agent-Key": VANTAGE_KEY}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        signals = data.get("signals", data)
        return [s for s in signals if s.get("source") == "ogun_degen"]
    except:
        return []

def execute_degen(symbol, conviction, amount_sol):
    """Execute Pump.fun scalp trade."""
    global TRADE_COUNT, CURRENT_BANKROLL
    
    token = symbol.lower().replace(" ", "-").replace("/", "-")[:20]
    print(f"\n{'='*50}")
    print(f"  🎯 DEGEN #{TRADE_COUNT+1}: {symbol}")
    print(f"  Conviction: {conviction:.2f} | Amount: {amount_sol:.4f} SOL")
    print(f"{'='*50}")
    
    try:
        cmd = f"sol token swap {amount_sol} sol {token} --wallet {WALLET}"
        print(f"  $ {cmd}")
        result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        
        sig = None
        for line in output.split("\n"):
            if "Signature:" in line:
                sig = line.split("Signature:")[-1].strip()
            if "Explorer:" in line:
                print(f"  {line.strip()}")
        
        if sig and "Error" not in output:
            TRADED.add(symbol)
            TRADE_COUNT += 1
            
            price = get_token_price(symbol) or 0
            
            db = sqlite3.connect(DB_PATH)
            db.execute("""
                INSERT INTO strategy_trades (strategy, symbol, side, amount_sol, entry_price, conviction, status, entry_time, notes)
                VALUES ('degen_loop', ?, 'BUY', ?, ?, ?, 'open', ?, ?)
            """, (symbol, amount_sol, price, conviction,
                  datetime.now(timezone.utc).isoformat(),
                  f"TX: {sig} | Vault: {get_vault_total():.4f} SOL"))
            db.commit()
            db.close()
            
            urllib.request.urlopen(urllib.request.Request(
                f"{VANTAGE_URL}/api/agents/posts/text",
                data=json.dumps({
                    "title": f"🎯 Degen #{TRADE_COUNT}: {symbol}",
                    "content": f"**Pump.fun Scalp**\n\nToken: {symbol}\nAmount: {amount_sol:.4f} SOL\nTX: {sig}\nVault: {get_vault_total():.4f} SOL",
                    "tags": ["trading","degen","pumpfun"], "status": "published", "content_type": "text"
                }).encode(),
                headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
            ), timeout=5)
            
            print(f"  ✅ LIVE: {sig[:20]}... | Vault: {get_vault_total():.4f} SOL")
            return sig
        else:
            print(f"  ❌ Failed: {output[:200]}")
            return None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None

def check_exits_and_compound():
    """Check open positions. On TP: split profit 50/50 vault/compound."""
    global CURRENT_BANKROLL
    
    db = sqlite3.connect(DB_PATH)
    trades = db.execute(
        "SELECT id, symbol, amount_sol, entry_price FROM strategy_trades WHERE strategy='degen_loop' AND status='open'"
    ).fetchall()
    
    for tid, symbol, amount, entry in trades:
        if not entry or entry == 0:
            continue
        
        price = get_token_price(symbol)
        if not price:
            continue
        
        pnl = (price - entry) / entry
        
        if pnl >= TAKE_PROFIT:
            print(f"\n  🎯 TAKE PROFIT: {symbol} @ +{pnl*100:.1f}%")
            
            # Sell entire position
            token = symbol.lower()
            result = subprocess.run(
                f"sol token swap all {token} sol --wallet {WALLET}".split(),
                capture_output=True, text=True, timeout=60
            )
            sig = None
            for line in (result.stdout + result.stderr).split("\n"):
                if "Signature:" in line:
                    sig = line.split("Signature:")[-1].strip()
            
            # Calculate profit and split
            profit_sol = amount * pnl  # Approximate profit in SOL
            vault_share = profit_sol * VAULT_PCT
            compound_share = profit_sol * COMPOUND_PCT
            
            # Vault deposit
            vault_deposit(vault_share, sig)
            
            # Compound: increase next trade size
            CURRENT_BANKROLL += compound_share
            print(f"  📈 COMPOUND: +{compound_share:.4f} SOL → next trade: {CURRENT_BANKROLL:.4f} SOL")
            print(f"  🏦 VAULT: +{vault_share:.4f} SOL → total: {get_vault_total():.4f} SOL")
            
            # Update DB
            db.execute(
                "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=?, notes=notes||? WHERE id=?",
                (price, pnl*100, datetime.now(timezone.utc).isoformat(),
                 f" | TP +{pnl*100:.1f}% Vault:{vault_share:.4f} Compound:{compound_share:.4f} TX:{sig}", tid)
            )
            db.commit()
            
            urllib.request.urlopen(urllib.request.Request(
                f"{VANTAGE_URL}/api/agents/posts/text",
                data=json.dumps({
                    "title": f"💰 Degen TP: {symbol} +{pnl*100:.0f}%",
                    "content": f"**Take Profit!**\n\n{symbol}: +{pnl*100:.1f}%\nVaulted: {vault_share:.4f} SOL\nCompounded: {compound_share:.4f} SOL\nNext trade: {CURRENT_BANKROLL:.4f} SOL\nTotal vault: {get_vault_total():.4f} SOL\nTX: {sig}",
                    "tags": ["trading","degen","profit"], "status": "published", "content_type": "text"
                }).encode(),
                headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
            ), timeout=5)
            
        elif pnl <= STOP_LOSS:
            print(f"\n  🛑 STOP LOSS: {symbol} @ {pnl*100:.1f}%")
            token = symbol.lower()
            result = subprocess.run(
                f"sol token swap all {token} sol --wallet {WALLET}".split(),
                capture_output=True, text=True, timeout=60
            )
            sig = None
            for line in (result.stdout + result.stderr).split("\n"):
                if "Signature:" in line:
                    sig = line.split("Signature:")[-1].strip()
            
            db.execute(
                "UPDATE strategy_trades SET status='closed', exit_price=?, pnl_pct=?, exit_time=?, notes=notes||? WHERE id=?",
                (price, pnl*100, datetime.now(timezone.utc).isoformat(),
                 f" | SL {pnl*100:.1f}% TX:{sig}", tid)
            )
            db.commit()
            
        else:
            print(f"  [{symbol}] {pnl*100:+.1f}% | Vault: {get_vault_total():.4f} SOL | Next: {CURRENT_BANKROLL:.4f} SOL", flush=True)
    
    db.close()

def show_status():
    print(f"\n{'='*50}")
    print(f"  DEGEN LOOP STATUS")
    print(f"  Trades: {TRADE_COUNT} | Vault: {get_vault_total():.4f} SOL")
    print(f"  Next trade: {CURRENT_BANKROLL:.4f} SOL (~${CURRENT_BANKROLL*81:.2f})")
    print(f"  Rules: TP +{TAKE_PROFIT*100:.0f}% | SL {STOP_LOSS*100:.0f}% | Split {VAULT_PCT*100:.0f}/{COMPOUND_PCT*100:.0f}")
    print(f"{'='*50}")

# ── Main Loop ─────────────────────────────────────────────────
def run():
    global CURRENT_BANKROLL
    
    init_db()
    vault = get_vault_total()
    if vault > 0:
        print(f"Resuming — vault: {vault:.4f} SOL")
    
    show_status()
    
    while True:
        try:
            # Check exits + compound
            check_exits_and_compound()
            
            # Find new degen signal
            signals = get_degen_signals()
            for sig in signals:
                symbol = sig.get("symbol", "?")
                conviction = sig.get("conviction", 0) or 0
                
                if conviction < MIN_CONVICTION or symbol in TRADED:
                    continue
                
                amount = min(CURRENT_BANKROLL, INITIAL_TRADE_SOL * 1.5)
                if amount < 0.001:
                    continue
                
                result = execute_degen(symbol, conviction, amount)
                
                # After trade, reduce bankroll
                if result:
                    CURRENT_BANKROLL -= amount
                    show_status()
            
            time.sleep(30)
            
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()
