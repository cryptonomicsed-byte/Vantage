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

def extract_and_route(text, chat_title="telegram"):
    """Extract wallet addresses and token CAs from text, route to watchlist/pumpfun."""
    import re as _re, sqlite3 as _sq, hashlib as _hl
    sol_addr = _re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')
    eth_addr = _re.compile(r'0x[a-fA-F0-9]{40}')
    btc_addr = _re.compile(r'[13][a-km-zA-HJ-NP-Z1-9]{25,34}')
    pumpfun_ca = _re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}pump')

    found = set()

    for addr in sol_addr.findall(text):
        if addr in found: continue
        found.add(addr)
        # Auto-detect via Helius
        try:
            import urllib.request as _ur, json as _j
            payload = _j.dumps({"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[addr,{"encoding":"jsonParsed"}]}).encode()
            req = _ur.Request("https://mainnet.helius-rpc.com/?api-key=3b16b895-d4f1-404b-8edd-f3be766830ca",data=payload,headers={"Content-Type":"application/json"})
            info = _j.loads(_ur.urlopen(req,timeout=5).read().decode()).get("result",{}).get("value",{})
            owner = info.get("owner","")
            program = info.get("data",{}).get("program","")
            if program in ("spl-token","spl-token-2022") or "pump" in addr.lower():
                # Token mint → pumpfun tracking
                try:
                    _ur.urlopen(_ur.Request("http://localhost:8001/api/intel/pumpfun/watchlist?mint="+addr+"&label="+chat_title[:20],
                        headers={"X-Agent-Key":"vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"}),timeout=3)
                    print(f"  📦 CA routed to pumpfun: {addr[:16]}...")
                except: pass
            elif owner in ("11111111111111111111111111111111","TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"):
                # Wallet → watchlist
                try:
                    payload2 = _j.dumps({"address":addr,"chain":"solana","label":f"Telegram: {chat_title[:20]}"}).encode()
                    _ur.urlopen(_ur.Request("http://localhost:8001/api/intel/watchlist",data=payload2,
                        headers={"Content-Type":"application/json","X-Agent-Key":"vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"}),timeout=3)
                    print(f"  👛 Wallet routed to watchlist: {addr[:16]}...")
                except: pass
        except: pass

    for addr in eth_addr.findall(text):
        if addr in found: continue
        found.add(addr)
        try:
            import urllib.request as _ur, json as _j
            payload = _j.dumps({"address":addr,"chain":"ethereum","label":f"Telegram: {chat_title[:20]}"}).encode()
            _ur.urlopen(_ur.Request("http://localhost:8001/api/intel/watchlist",data=payload,
                headers={"Content-Type":"application/json","X-Agent-Key":"vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"}),timeout=3)
            print(f"  👛 ETH wallet routed: {addr[:16]}...")
        except: pass

    for addr in btc_addr.findall(text):
        if addr in found: continue
        found.add(addr)
        try:
            import urllib.request as _ur, json as _j
            payload = _j.dumps({"address":addr,"chain":"bitcoin","label":f"Telegram: {chat_title[:20]}"}).encode()
            _ur.urlopen(_ur.Request("http://localhost:8001/api/intel/watchlist",data=payload,
                headers={"Content-Type":"application/json","X-Agent-Key":"vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f"}),timeout=3)
            print(f"  👛 BTC wallet routed: {addr[:16]}...")
        except: pass

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
            # Also extract addresses from the message
            extract_and_route(text, chat_title)
            return

    print(f"📝 No pattern: {text[:100]}")
    # Still try to extract addresses even without a trading signal
    extract_and_route(text, chat_title)

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
