"""Telegram Webhook Handler — Receive signals from any channel via @Omokoda_bot."""
import json, re, urllib.request, sqlite3
from pathlib import Path
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/telegram", tags=["telegram"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
VANTAGE_URL = "http://localhost:8001/api/trading/signals/ingest"

PATTERNS = [
    (re.compile(r'(BUY|SELL|LONG|SHORT)\s+\$?(\w{2,10})', re.I), 0.6),
    (re.compile(r'\b(LONG|SHORT)\b.*?\$?(\w{2,10})', re.I), 0.7),
    (re.compile(r'\$(\w{2,10})\s*(?:entry|target|tp|take profit).*?\$?([\d.]+)', re.I), 0.5),
    (re.compile(r'(?:buy|sell|long|short)\s+\$?(\w{2,10})', re.I), 0.6),
    (re.compile(r'🚀.*?\$(\w{2,10})', re.I), 0.5),
    (re.compile(r'[Cc]onviction[:\s]+(\d+)', re.I), 0.7),
]

def parse_and_ingest(text, chat_title="telegram"):
    """Parse text for trading signals and ingest into Vantage."""
    for pattern, base_conviction in PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            symbol = next((g for g in groups if g and 2 <= len(str(g)) <= 10 and str(g).isalpha()), None)
            direction = next((str(g).upper() for g in groups if str(g).upper() in ('BUY','SELL','LONG','SHORT')), "BUY")
            conviction = next((float(g)/10 for g in groups if g and str(g).isdigit() and 1 <= int(g) <= 10), base_conviction)

            if not symbol:
                continue

            # Boost conviction based on message content
            if any(w in text.lower() for w in ('confirmed','massive','100x','gem','alpha')):
                conviction = min(1.0, conviction + 0.2)
            if any(w in text.lower() for w in ('urgent','now','immediately')):
                conviction = min(1.0, conviction + 0.1)

            signal = dict(
                symbol=symbol.upper(),
                direction=direction,
                conviction=conviction,
                type="telegram_alpha",
                detail=f"From {chat_title}: {text[:300]}",
                source="telegram_bot",
            )

            try:
                payload = json.dumps(signal).encode()
                req = urllib.request.Request(VANTAGE_URL, data=payload, headers={"Content-Type":"application/json","X-Agent-Key":"vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"})
                urllib.request.urlopen(req, timeout=5)
                print(f"✅ {symbol} {direction} c={conviction}")
            except:
                pass
            return

    print(f"📝 No pattern: {text[:100]}")

@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram messages forwarded to @Omokoda_bot."""
    try:
        data = await request.json()
        msg = data.get("message", {}) or data.get("channel_post", {})
        text = msg.get("text", "") or msg.get("caption", "")
        chat = msg.get("chat", {})
        chat_title = chat.get("title", chat.get("username", "unknown"))

        if text:
            parse_and_ingest(text, chat_title)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:100]}
