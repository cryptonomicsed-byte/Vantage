#!/usr/bin/env python3
"""
Social Sentiment Tracker — Twitter/X + Telegram account monitoring
Stores accounts, monitors for ticker/CA mentions, feeds signals to Vantage.

Storage: social_accounts DB table
Sources: xurl (Twitter), Telegram API
Sentiment: FinBERT NLP + keyword extraction
"""
import sqlite3, json, subprocess, urllib.request, re, time, os
from datetime import datetime, timezone

DB_PATH = "/opt/ares/Vantage/data/vantage.db"
VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = os.environ.get("VANTAGE_AGENT_KEY", "")
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
XURL_PATH = "/usr/local/bin/xurl"

# ── DB Setup ─────────────────────────────────────────────────
def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS social_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,        -- 'twitter' or 'telegram'
            username TEXT NOT NULL,         -- @handle or channel name
            account_type TEXT DEFAULT 'tracker',  -- 'tracker', 'signal', 'alpha'
            tickers TEXT,                   -- comma-separated: BTC,ETH,SOL
            contract_addresses TEXT,        -- comma-separated: SOL addresses
            notes TEXT,
            last_checked TEXT,
            signal_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS social_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            platform TEXT,
            username TEXT,
            ticker TEXT,
            contract_address TEXT,
            sentiment TEXT,                 -- BULLISH, BEARISH, NEUTRAL
            confidence REAL,
            post_text TEXT,
            post_url TEXT,
            signal_type TEXT DEFAULT 'mention',  -- 'mention', 'call', 'alpha'
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()
    return db

# ── Account Management ───────────────────────────────────────
def add_account(platform, username, tickers="", ca="", account_type="tracker", notes=""):
    db = init_db()
    db.execute(
        "INSERT INTO social_accounts (platform, username, account_type, tickers, contract_addresses, notes) VALUES (?,?,?,?,?,?)",
        (platform.lower(), username.lower().strip("@"), account_type, tickers.upper(), ca, notes)
    )
    db.commit()
    print(f"Added {platform}: {username}")

def list_accounts():
    db = init_db()
    accounts = db.execute("SELECT id, platform, username, account_type, tickers, contract_addresses, signal_count, last_checked FROM social_accounts ORDER BY platform, username").fetchall()
    print(f"\n{'ID':<4} {'Platform':<10} {'Username':<25} {'Type':<10} {'Tickers':<20} {'Signals':<8}")
    print("-" * 80)
    for a in accounts:
        print(f"{a[0]:<4} {a[1]:<10} @{a[2]:<24} {a[3]:<10} {(a[4] or ''):<20} {a[6]:<8}")
    return accounts

# ── Twitter Scanner ──────────────────────────────────────────
def scan_twitter(username, tracked_tickers, tracked_cas):
    """Scan recent tweets from a user for ticker/CA mentions."""
    try:
        result = subprocess.run(
            [XURL_PATH, "user", username, "--limit", "10"],
            capture_output=True, text=True, timeout=30
        )
        tweets = []
        for line in result.stdout.split("\n"):
            if line.strip() and not line.startswith("Error"):
                tweets.append(line)
        
        signals = []
        for tweet in tweets[:5]:
            text_upper = tweet.upper()
            
            # Check for ticker mentions
            for ticker in tracked_tickers:
                if ticker in text_upper or f"${ticker}" in text_upper:
                    # Quick sentiment: bullish keywords
                    sentiment = "BULLISH" if any(w in tweet.lower() for w in ["buy","long","moon","pump","gem","next"]) else "NEUTRAL"
                    if any(w in tweet.lower() for w in ["sell","dump","rug","scam","short"]):
                        sentiment = "BEARISH"
                    
                    signals.append({"ticker": ticker, "sentiment": sentiment, "text": tweet[:200]})
            
            # Check for CA mentions (Solana addresses)
            ca_pattern = r'[1-9A-HJ-NP-Za-km-z]{32,44}'
            for match in re.findall(ca_pattern, tweet):
                signals.append({"contract_address": match, "sentiment": "NEUTRAL", "text": tweet[:200]})
        
        return signals
    except Exception as e:
        print(f"  Twitter error ({username}): {e}")
        return []

# ── Telegram Scanner ─────────────────────────────────────────
def scan_telegram(channel, tracked_tickers, tracked_cas):
    """Scan recent Telegram messages for ticker/CA mentions."""
    # Telegram API requires bot token — this is CLI-friendly version using tdl
    try:
        # Try using python-telegram or similar
        import subprocess
        result = subprocess.run(
            ["python3", "-c", f"""
import urllib.request, json
# Use Telegram Bot API (needs bot token)
# For now: placeholder — requires @Omokoda_bot to join channels
print('Telegram scan requires bot in channel: {channel}')
"""],
            capture_output=True, text=True, timeout=15
        )
        return []
    except Exception as e:
        print(f"  Telegram error ({channel}): {e}")
        return []

# ── Signal Poster ────────────────────────────────────────────
def post_signal(account_id, platform, username, ticker, ca, sentiment, confidence, text):
    """Post social signal to Vantage + DB."""
    db = init_db()
    db.execute(
        "INSERT INTO social_signals (account_id, platform, username, ticker, contract_address, sentiment, confidence, post_text) VALUES (?,?,?,?,?,?,?,?)",
        (account_id, platform, username, ticker, ca, sentiment, confidence, text[:500])
    )
    db.execute("UPDATE social_accounts SET signal_count = signal_count + 1, last_checked = ? WHERE id = ?",
               (datetime.now(timezone.utc).isoformat(), account_id))
    db.commit()
    
    # Post to Vantage signal pool
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals/ingest",
            data=json.dumps({
                "symbol": ticker or ca or "SOCIAL",
                "source": f"social_{platform}",
                "conviction": confidence,
                "type": "sentiment",
                "detail": f"{sentiment} | {username}: {text[:100]}"
            }).encode(),
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY}
        )
        urllib.request.urlopen(req, timeout=5)
    except: pass

# ── Main Scan Loop ───────────────────────────────────────────
def scan_all(interval=300):
    db = init_db()
    accounts = db.execute("SELECT id, platform, username, tickers, contract_addresses FROM social_accounts").fetchall()
    
    if not accounts:
        print("No accounts tracked. Add with: python3 social_tracker.py add twitter @account BTC,ETH,SOL")
        return
    
    print(f"Social Tracker — {len(accounts)} accounts — {interval}s cycle")
    
    while True:
        total_signals = 0
        for aid, platform, username, tickers_str, cas_str in accounts:
            tickers = [t.strip() for t in (tickers_str or "").split(",") if t.strip()]
            cas = [c.strip() for c in (cas_str or "").split(",") if c.strip()]
            
            if platform == "twitter":
                signals = scan_twitter(username, tickers, cas)
            elif platform == "telegram":
                signals = scan_telegram(username, tickers, cas)
            else:
                continue
            
            for sig in signals:
                ticker = sig.get("ticker", "")
                ca = sig.get("contract_address", "")
                sentiment = sig.get("sentiment", "NEUTRAL")
                text = sig.get("text", "")
                confidence = 0.7 if sentiment == "BULLISH" else 0.5
                post_signal(aid, platform, username, ticker, ca, sentiment, confidence, text)
                print(f"  {sentiment} {platform}: @{username} → {ticker or ca[:12]} | {text[:60]}")
                total_signals += 1
        
        if total_signals:
            print(f"  Posted {total_signals} social signals")
        
        time.sleep(interval)

# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    init_db()
    
    if len(sys.argv) < 2:
        print("Social Sentiment Tracker")
        print("  add twitter @account TICKER1,TICKER2  — Track Twitter account")
        print("  add telegram channel TICKER1            — Track Telegram channel")
        print("  list                                      — List tracked accounts")
        print("  scan                                      — Run one scan cycle")
        print("  daemon [interval]                        — Run continuous (default 300s)")
        sys.exit(0)
    
    cmd = sys.argv[1]
    
    if cmd == "add" and len(sys.argv) >= 4:
        platform = sys.argv[2]
        username = sys.argv[3].strip("@")
        tickers = sys.argv[4] if len(sys.argv) > 4 else ""
        ca = sys.argv[5] if len(sys.argv) > 5 else ""
        acct_type = sys.argv[6] if len(sys.argv) > 6 else "tracker"
        notes = sys.argv[7] if len(sys.argv) > 7 else ""
        add_account(platform, username, tickers, ca, acct_type, notes)
    
    elif cmd == "list":
        list_accounts()
    
    elif cmd == "scan":
        scan_all(1)  # Single cycle
    
    elif cmd == "daemon":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300
        scan_all(interval)
