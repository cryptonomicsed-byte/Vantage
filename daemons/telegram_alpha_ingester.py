#!/opt/ares/venv/bin/python3
"""telegram_alpha_ingester — Monitors Telegram channels for trading alpha.
Receives forwarded messages or channel reads via @Omokoda_bot.
Parses symbols, directions, conviction from message text.
Posts signals to Vantage trading_signals.

Usage: telegram_alpha_ingester.py [--daemon]
Configure: Add channels to CHANNELS list below
"""
import json, re, sqlite3, os, sys, signal, time, urllib.request

# ── Config ──────────────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
VANTAGE_URL = "http://localhost:8001/api/trading/signals/ingest"
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
DB = "/opt/ares/Vantage/data/vantage.db"

# Channels to monitor (username or invite link)
CHANNELS = [
    "@AlphaLabx",
    # Add more channels here:
    # "@WhaleAlertCrypto",
    # "@DeFiAlphaCalls"
    "@gmgnapp_official",
    "@delugecash",
    "@trendingssol",
    "@buybot",
    "@handsomertg",
    "@lynkspump",,
]

# ── Signal Patterns ─────────────────────────────────────────────────
PATTERNS = [
    # "BUY $SYMBOL at $0.005 target $0.05"
    re.compile(r'(BUY|SELL|LONG|SHORT)\s+\$?(\w{2,10})\s+(?:at\s+)?\$?([\d.]+)', re.I),
    # "$SYMBOL/USDT - LONG - Target: $5"
    re.compile(r'\$?(\w{2,10})[/-]?(USD[TC]?)?\s*[-–]\s*(LONG|SHORT|BUY|SELL)', re.I),
    # "Symbol: $PEPE | Direction: BUY | Conviction: 8/10"
    re.compile(r'[Ss]ymbol[:\s]+\$?(\w{2,10}).*?[Dd]irection[:\s]+(LONG|SHORT|BUY|SELL).*?[Cc]onviction[:\s]+(\d+)', re.I | re.DOTALL),
    # "🚀 $WIF is pumping - enter now!"
    re.compile(r'[\$](\w{2,10})\s+is\s+pumping', re.I),
    # General: any $SYMBOL mentioned with target or entry price
    re.compile(r'\$(\w{2,10})\b.*?\$?([\d.]+)\s*(?:entry|target|buy|tp|take profit)', re.I),
]

def parse_signal(text, channel_name="unknown"):
    """Extract trading signal from Telegram message text."""
    for pattern in PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            symbol = next((g for g in groups if g and len(str(g)) <= 10 and str(g).isalpha()), "")
            direction = next((g for g in groups if str(g).upper() in ('BUY','SELL','LONG','SHORT')), "BUY")
            price = next((g for g in groups if g and re.match(r'[\d.]+$', str(g))), None)
            
            if symbol:
                symbol = symbol.upper()
                conviction = 0.5  # Default
                
                # Boost conviction based on keywords
                if any(w in text.lower() for w in ('confirmed','breaking','massive','100x','gem','alpha')):
                    conviction = 0.7
                if any(w in text.lower() for w in ('urgent','now','immediately')):
                    conviction = 0.8
                
                return {
                    "symbol": symbol,
                    "direction": direction.upper(),
                    "conviction": conviction,
                    "type": "telegram_alpha",
                    "detail": f"From {channel_name}: {text[:200]}",
                    "source": "telegram_alpha_ingester",
                }
    return None

def ingest(signal):
    """Post signal to Vantage."""
    try:
        payload = json.dumps(signal).encode()
        req = urllib.request.Request(VANTAGE_URL, data=payload, headers={
            "Content-Type": "application/json",
            "X-Agent-Key": VANTAGE_KEY,
        })
        urllib.request.urlopen(req, timeout=5)
        print(f"  ✅ {signal['symbol']} {signal['direction']} (conv={signal['conviction']})")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False

# ── Telegram polling (via getUpdates) ───────────────────────────────
def poll_updates():
    """Poll Telegram for new messages from monitored channels."""
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?timeout=30&offset={offset}"
            req = urllib.request.Request(url)
            data = json.loads(urllib.request.urlopen(req, timeout=35).read().decode())
            
            updates = data.get("result", [])
            if updates:
                for update in updates:
                    offset = update["update_id"] + 1
                    
                    # Handle forwarded messages or channel posts
                    msg = update.get("message", {}) or update.get("channel_post", {})
                    text = msg.get("text", "") or msg.get("caption", "")
                    chat = msg.get("chat", {})
                    chat_title = chat.get("title", chat.get("username", "unknown"))
                    
                    if not text:
                        continue
                    
                    # Check if from monitored channel
                    chat_username = chat.get("username", "")
                    if CHANNELS and not any(
                        ch.strip("@") in (chat_title, chat_username) 
                        for ch in CHANNELS
                    ):
                        continue
                    
                    print(f"\n  📨 [{chat_title}] {text[:100]}...")
                    
                    # Parse signal
                    signal = parse_signal(text, chat_title)
                    if signal:
                        ingest(signal)
                    else:
                        # Log first 80 chars of non-signal messages
                        print(f"  📝 No signal pattern: {text[:80]}")
            
        except Exception as e:
            print(f"  ⚠️ Poll error: {e}")
            time.sleep(10)

# ── Main ────────────────────────────────────────────────────────────
def run():
    print("═══ telegram_alpha_ingester v1 ═══")
    print(f"  Bot: @{json.loads(urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{TOKEN}/getMe')))}")
    print(f"  Channels: {CHANNELS}")
    print(f"  Waiting for signals...")
    print()
    
    poll_updates()

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        pid = os.fork()
        if pid > 0: sys.exit(0)
        os.setsid()
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run()
