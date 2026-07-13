"""Telegram Webhook Handler — Receive updates from @Omokoda_bot.

Two jobs, both driven by the same incoming message:

1. Trading-signal parsing (BUY/SELL/LONG/SHORT patterns) — existing
   behavior, auth bug fixed below (was posting with X-Agent-Key to a
   system-tool-only endpoint, so every signal silently 401'd).

2. Watchlist group monitoring — for Telegram GROUPS on the social_accounts
   watchlist (account_type='group': SensusHQ, gmgnapp_official, lynkspump,
   zancaban). These can't be scraped via t.me/s/<channel> (that public
   preview endpoint only works for broadcast channels, not groups), so
   for groups specifically we rely on this bot being a real member and
   forwarding real-time updates here. Reuses social_tracker.py's mention
   extraction + on-chain PnL backtracking so group chatter lands in the
   money-flow graph exactly like channel scans do.

   Requires: bot added to the group by a human (can't join itself), and
   Group Privacy turned OFF in @BotFather (else Telegram only forwards
   messages that start with a /command).
"""
import asyncio, json, os, re, sys, urllib.request
from pathlib import Path
from fastapi import APIRouter, Request

sys.path.insert(0, "/opt/ares")
import social_tracker  # noqa: E402  (reuses _extract_mentions/post_signal/init_db)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])
DB = Path("/opt/ares/Vantage/data/vantage.db")
# NOT /api/trading/signals/ingest: that endpoint auto-executes a real trade
# when conviction > 0.7 and a wallet exists for the target agent. Piping
# regex-matched text from a Telegram group straight into auto-execution
# would mean an unverified group post could trigger a real buy. This goes
# to the intel signal pool instead (same target social_tracker.py uses) —
# visible, scored, not blindly executed.
INTEL_INGEST_URL = "http://localhost:8001/api/intel/signals/ingest"
TOOL_INTEL_KEY = os.environ.get("VANTAGE_TOOL_INTEL_KEY", os.environ.get("VANTAGE_TOOL_INTEL", ""))

PATTERNS = [
    (re.compile(r'(BUY|SELL|LONG|SHORT)\s+\$?(\w{2,10})', re.I), 0.6),
    (re.compile(r'\b(LONG|SHORT)\b.*?\$?(\w{2,10})', re.I), 0.7),
    (re.compile(r'\$(\w{2,10})\s*(?:entry|target|tp|take profit).*?\$?([\d.]+)', re.I), 0.5),
    (re.compile(r'(?:buy|sell|long|short)\s+\$?(\w{2,10})', re.I), 0.6),
    (re.compile(r'🚀.*?\$(\w{2,10})', re.I), 0.5),
    (re.compile(r'[Cc]onviction[:\s]+(\d+)', re.I), 0.7),
]


def _find_group_account(db, chat_username, chat_title):
    candidates = {(chat_username or "").lower().lstrip("@"), (chat_title or "").lower()}
    rows = db.execute(
        "SELECT id, username, tickers, contract_addresses FROM social_accounts "
        "WHERE platform='telegram' AND account_type='group'"
    ).fetchall()
    for aid, uname, tickers, cas in rows:
        if uname.lower() in candidates:
            return aid, uname, tickers, cas
    return None


def parse_and_ingest_trade_signal(text, chat_title="telegram"):
    """BUY/SELL/LONG/SHORT pattern parse -> intel signal pool (not auto-exec)."""
    if not TOOL_INTEL_KEY:
        return
    for pattern, base_conviction in PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            symbol = next((g for g in groups if g and 2 <= len(str(g)) <= 10 and str(g).isalpha()), None)
            direction = next((str(g).upper() for g in groups if str(g).upper() in ('BUY', 'SELL', 'LONG', 'SHORT')), "BUY")
            conviction = next((float(g) / 10 for g in groups if g and str(g).isdigit() and 1 <= int(g) <= 10), base_conviction)
            if not symbol:
                continue
            if any(w in text.lower() for w in ('confirmed', 'massive', '100x', 'gem', 'alpha')):
                conviction = min(1.0, conviction + 0.2)
            if any(w in text.lower() for w in ('urgent', 'now', 'immediately')):
                conviction = min(1.0, conviction + 0.1)

            signal = dict(
                symbol=symbol.upper(), source="telegram_bot",
                conviction=conviction, type="telegram_alpha",
                detail=f"{direction} | From {chat_title}: {text[:300]}",
            )
            try:
                req = urllib.request.Request(
                    INTEL_INGEST_URL, data=json.dumps(signal).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "X-Vantage-Tool": "intel",
                        "X-Vantage-Tool-Key": TOOL_INTEL_KEY,
                    },
                )
                urllib.request.urlopen(req, timeout=5)
                print(f"intel signal: {symbol} {direction} c={conviction}", flush=True)
            except Exception as e:
                print(f"intel signal post failed: {e}", flush=True)
            return

    # No regex pattern matched — same paraphrased-call gap as
    # social_tracker.py's _extract_mentions, same fix: only spend an LLM
    # call on text regex already gave up on.
    try:
        import llm_extract
        llm_sig = llm_extract.extract_signal_llm(text)
    except Exception:
        llm_sig = None
    if llm_sig and llm_sig.ticker:
        conviction = llm_sig.confidence
        if llm_sig.direction == "BEARISH":
            conviction = min(conviction, 0.5)
        signal = dict(
            symbol=llm_sig.ticker.upper(), source="telegram_bot_llm",
            conviction=conviction, type="telegram_alpha",
            detail=f"{llm_sig.direction} | From {chat_title}: {llm_sig.reasoning} — \"{text[:200]}\"",
        )
        try:
            req = urllib.request.Request(
                INTEL_INGEST_URL, data=json.dumps(signal).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Vantage-Tool": "intel",
                    "X-Vantage-Tool-Key": TOOL_INTEL_KEY,
                },
            )
            urllib.request.urlopen(req, timeout=5)
            print(f"intel signal (llm): {llm_sig.ticker} {llm_sig.direction} c={conviction}", flush=True)
        except Exception as e:
            print(f"intel signal (llm) post failed: {e}", flush=True)


def ingest_watchlist_group_message(text, chat_username, chat_title, post_url):
    """If this message came from one of our tracked Telegram GROUPS, run it
    through the same mention-extraction + PnL-backtracking pipeline the
    channel scanner uses, so group alpha lands in the money-flow graph."""
    db = social_tracker.init_db()
    hit = _find_group_account(db, chat_username, chat_title)
    if not hit:
        return
    account_id, uname, tickers_str, cas_str = hit
    tracked_tickers = [t.strip() for t in (tickers_str or "").split(",") if t.strip()]
    tracked_cas = [c.strip() for c in (cas_str or "").split(",") if c.strip()]
    for sig in social_tracker._extract_mentions(text, tracked_tickers, tracked_cas):
        social_tracker.post_signal(
            db, account_id, "telegram", uname,
            sig.get("ticker", ""), sig.get("contract_address", ""),
            sig.get("sentiment", "NEUTRAL"),
            0.7 if sig.get("sentiment") == "BULLISH" else 0.5,
            sig.get("text", text[:400]), post_url,
        )
        print(f"group signal: @{uname} -> {sig.get('ticker') or sig.get('contract_address', '')[:12]}", flush=True)


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram messages forwarded to @Omokoda_bot (DMs, channels
    it's posted to, and groups it has been added to with privacy off)."""
    try:
        data = await request.json()
        msg = data.get("message", {}) or data.get("channel_post", {})
        text = msg.get("text", "") or msg.get("caption", "")
        chat = msg.get("chat", {})
        chat_title = chat.get("title", chat.get("username", "unknown"))
        chat_username = chat.get("username", "")
        chat_type = chat.get("type", "")
        msg_id = msg.get("message_id", "")
        post_url = f"https://t.me/{chat_username}/{msg_id}" if chat_username else ""

        if chat_type in ("group", "supergroup"):
            print(f"group update: @{chat_username or chat_title} type={chat_type} text={text[:120]!r}", flush=True)

        if text:
            # These call back into Vantage's own HTTP API (localhost:8001)
            # with blocking urllib — run off the event loop or the single
            # worker deadlocks answering its own request (confirmed: was
            # timing out every call before this fix).
            await asyncio.to_thread(parse_and_ingest_trade_signal, text, chat_title)
            if chat_type in ("group", "supergroup"):
                await asyncio.to_thread(ingest_watchlist_group_message, text, chat_username, chat_title, post_url)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:100]}
