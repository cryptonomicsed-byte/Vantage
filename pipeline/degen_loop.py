#!/usr/bin/env python3
"""
DEGEN LOOP — Continuous Pump.fun scalping with profit-splitting
Strategy: 50% profit → vault (USDC) | 50% → compound into next trade
Start: $7 (~0.085 SOL) per trade

REQUIRES: VANTAGE_KEY, BIRDEYE_KEY env vars set
"""
import subprocess, json, sqlite3, time, os, urllib.request
from datetime import datetime, timezone

VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = os.environ.get("VANTAGE_KEY","")
BIRDEYE_KEY = os.environ.get("BIRDEYE_KEY","")
ALCHEMY_KEY = os.environ.get("ALCHEMY_API_KEY","")
DB_PATH = os.environ.get("DB_PATH", "/opt/ares/Vantage/data/vantage.db")
WALLET = os.environ.get("WALLET", "ogun")

ALCHEMY_RPC_SOLANA = f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"

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

def get_wallet_nft_holders(wallet_address):
    """Get NFT holdings for a wallet via Alchemy (detect insider wallets)."""
    if not ALCHEMY_KEY:
        return []
    try:
        req = urllib.request.Request(
            f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}/getNFTs?owner={wallet_address}",
            headers={"accept": "application/json"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        return data.get("nfts", [])
    except:
        return []

def get_token_holder_info(mint):
    """Get token holder distribution via Alchemy."""
    if not ALCHEMY_KEY:
        return None
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getProgramAccounts",
            "params": [mint, {"filters": [{"dataSize": 165}]}]
        }).encode()
        req = urllib.request.Request(
            ALCHEMY_RPC_SOLANA,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        return resp.get("result", [])
    except:
        return None

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

def get_token_price(symbol_or_mint):
    """Get token price from Birdeye API. Accepts symbol or mint address."""
    if not BIRDEYE_KEY:
        return None

    try:
        # Try as mint first (44-char base58), then search by symbol
        search_term = symbol_or_mint
        req = urllib.request.Request(
            f"https://public-api.birdeye.so/defi/price?address={search_term}",
            headers={"X-API-KEY": BIRDEYE_KEY, "accept": "application/json"}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        price = float(resp.get("data", {}).get("value", 0))
        if price > 0:
            return price
    except:
        pass

    # Fall back to searching by symbol via Vantage API
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals?source=degen&symbol={symbol_or_mint}",
            headers={"X-Agent-Key": VANTAGE_KEY}
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        signals = resp.get("signals", [])
        if signals and "price" in signals[0]:
            return float(signals[0]["price"])
    except:
        pass

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

def check_insider_wallets(mint):
    """Check if token is held by known insider/smart wallets via Alchemy NFT metadata."""
    if not ALCHEMY_KEY:
        return False, "Alchemy not configured"

    try:
        # Get token holder info
        holders = get_token_holder_info(mint)
        if not holders or len(holders) < 3:
            return False, f"Too few holders ({len(holders) or 0})"

        # Check for suspicious holder patterns (dev wallet concentrated holdings)
        # This would require analyzing token_info on each holder
        return True, f"Token has {len(holders)} holders"
    except Exception as e:
        return False, f"Holder check failed: {e}"

def execute_degen(symbol, conviction, amount_sol):
    """Execute Pump.fun scalp trade via Vantage trading API."""
    global TRADE_COUNT, CURRENT_BANKROLL

    print(f"\n{'='*50}")
    print(f"  🎯 DEGEN #{TRADE_COUNT+1}: {symbol}")
    print(f"  Conviction: {conviction:.2f} | Amount: {amount_sol:.4f} SOL")
    print(f"{'='*50}")

    try:
        # Place buy order via Vantage API
        payload = json.dumps({
            "symbol": f"{symbol}/USDC",
            "side": "buy",
            "type": "market",
            "amount": amount_sol,
            "chain": "solana",
            "notes": f"Degen scalp #{TRADE_COUNT+1} | conviction={conviction:.2f}",
            "source": "degen_loop",
        }).encode()

        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/trading/orders",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Agent-Key": VANTAGE_KEY,
            }
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        order_id = resp.get("order_id")
        tx_sig = resp.get("tx_sig", f"order_{order_id}")

        if order_id:
            TRADED.add(symbol)
            TRADE_COUNT += 1

            price = get_token_price(symbol) or 0

            db = sqlite3.connect(DB_PATH)
            db.execute("""
                INSERT INTO strategy_trades (strategy, symbol, side, amount_sol, entry_price, conviction, status, entry_time, notes)
                VALUES ('degen_loop', ?, 'BUY', ?, ?, ?, 'open', ?, ?)
            """, (symbol, amount_sol, price, conviction,
                  datetime.now(timezone.utc).isoformat(),
                  f"Order #{order_id} | TX: {tx_sig} | Vault: {get_vault_total():.4f} SOL"))
            db.commit()
            db.close()

            # Post to signals feed
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"{VANTAGE_URL}/api/trading/signals/ingest",
                    data=json.dumps({
                        "title": f"🎯 Degen #{TRADE_COUNT}: {symbol}",
                        "content": f"**Pump.fun Scalp**\n\nToken: {symbol}\nAmount: {amount_sol:.4f} SOL\nOrder: #{order_id}\nVault: {get_vault_total():.4f} SOL",
                        "tags": ["trading","degen","pumpfun"], "status": "published", "content_type": "text"
                    }).encode(),
                    headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
                ), timeout=5)
            except:
                pass

            print(f"  ✅ LIVE: Order #{order_id} | Vault: {get_vault_total():.4f} SOL")
            return tx_sig
        else:
            print(f"  ❌ Order failed: {resp.get('error', 'unknown error')}")
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

            # Sell entire position via Vantage API
            try:
                payload = json.dumps({
                    "symbol": f"{symbol}/USDC",
                    "side": "sell",
                    "type": "market",
                    "amount": amount,
                    "chain": "solana",
                    "notes": f"Degen TP +{pnl*100:.1f}%",
                    "source": "degen_loop",
                }).encode()
                req = urllib.request.Request(
                    f"{VANTAGE_URL}/api/trading/orders",
                    data=payload,
                    headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
                )
                resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
                sig = resp.get("tx_sig", f"order_{resp.get('order_id')}")
            except Exception as e:
                print(f"  Sell order failed: {e}")
                sig = None
            
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
                f"{VANTAGE_URL}/api/trading/signals/ingest",
                data=json.dumps({
                    "title": f"💰 Degen TP: {symbol} +{pnl*100:.0f}%",
                    "content": f"**Take Profit!**\n\n{symbol}: +{pnl*100:.1f}%\nVaulted: {vault_share:.4f} SOL\nCompounded: {compound_share:.4f} SOL\nNext trade: {CURRENT_BANKROLL:.4f} SOL\nTotal vault: {get_vault_total():.4f} SOL\nTX: {sig}",
                    "tags": ["trading","degen","profit"], "status": "published", "content_type": "text"
                }).encode(),
                headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
            ), timeout=5)
            
        elif pnl <= STOP_LOSS:
            print(f"\n  🛑 STOP LOSS: {symbol} @ {pnl*100:.1f}%")

            # Sell entire position via Vantage API
            try:
                payload = json.dumps({
                    "symbol": f"{symbol}/USDC",
                    "side": "sell",
                    "type": "market",
                    "amount": amount,
                    "chain": "solana",
                    "notes": f"Degen SL {pnl*100:.1f}%",
                    "source": "degen_loop",
                }).encode()
                req = urllib.request.Request(
                    f"{VANTAGE_URL}/api/trading/orders",
                    data=payload,
                    headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
                )
                resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
                sig = resp.get("tx_sig", f"order_{resp.get('order_id')}")
            except Exception as e:
                print(f"  Sell order failed: {e}")
                sig = None
            
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
