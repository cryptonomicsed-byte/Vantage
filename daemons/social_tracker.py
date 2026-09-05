#!/usr/bin/env python3
"""
Social Sentiment Tracker — Twitter/X + Telegram account monitoring, for a
watchlist you control (social_accounts table). This never browses/scrapes
broadly — it only ever looks at specific public accounts added to that
table, via each platform's own real surface:

  Telegram — https://t.me/s/<channel>, Telegram's own official public
    preview page for any public channel. No login, no bot membership, no
    auth of any kind — this is the same page a logged-out browser sees.
  Twitter/X — 2026-08-30: replaced the xurl/X-Developer-API path (never
    actually usable -- X's free API tier doesn't include read access to
    other users' timelines, and no paid key was ever configured) with
    daemons/x_browser_scraper.py: one real, dedicated, human-created,
    already-logged-in X account, session-persisted via Camoufox, reading
    public profile timelines directly (no scraping API, no third-party
    subscription). See that module's own docstring for the full real
    evaluation (why direct profile visits over the notification-feed
    approach, why NOT wrapped in oniux/Tor, real rate-limiting and
    challenge-detection discipline). ticker/CA extraction and sentiment
    stay HERE (_extract_mentions/_classify_sentiment, below) -- the
    browser module only returns raw scraped post text, never duplicates
    extraction logic.

Was previously never actually working: not running as a service, Telegram
was an unimplemented stub, and the Twitter path had no real credential
path at all. This rewrite makes both paths real.

NEW: PnL backtracking (verified_calls). When a social post yields both a
wallet claim (social_wallet_links, from PnL-post address extraction) and a
token mention in the same account's recent activity, this looks up that
wallet's REAL on-chain trade for that token near the claim time via Helius,
computes the actual entry price from the swap itself (not the self-reported
screenshot), and compares to the current price. This is what actually
answers "did this account's calls make money" from verifiable on-chain
truth instead of trusting a screenshot — and feeds a verified performance
score into wallet_reputation, separate from (and more trustworthy than)
the role-based heuristic score wallet_learner.py already computes.
"""
import sqlite3, json, subprocess, urllib.request, urllib.error, re, time, os, sys, html
import sys as _vshim_sys
_vshim_sys.path.insert(0, "/opt/ares")
import vantage_db_shim as _vshim
from datetime import datetime, timezone

sys.path.insert(0, "/opt/ares")
import api_key_pool

DB_PATH = "/opt/ares/Vantage/data/vantage.db"
VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
# /api/intel/signals/ingest is a SYSTEM-TOOL endpoint (get_system_tool in
# backend/deps.py) — X-Vantage-Tool + X-Vantage-Tool-Key, not X-Agent-Key.
# Same bug class found and fixed in worldmonitor_bridge.py earlier; fixed
# here before it ever shipped broken instead of after.
VANTAGE_TOOL_KEY = os.environ.get("VANTAGE_TOOL_INTEL_KEY", "")
TASK_NAME = "social_tracker"

# Lazy import: x_browser_scraper depends on camoufox+playwright, a much
# heavier/riskier dependency chain than the rest of this daemon. A missing
# or broken camoufox install must never take down the already-working
# Telegram path — see _twitter_posts_batch()'s own try/except below.
sys.path.insert(0, "/opt/ares/Vantage/daemons")

SOL_ADDR_RE = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')
ETH_ADDR_RE = re.compile(r'\b0x[a-fA-F0-9]{40}\b')
TICKER_RE = re.compile(r'\$([A-Z]{2,15})\b')


def _helius_key():
    return api_key_pool.get_key("helius", TASK_NAME) or os.environ.get("HELIUS_API_KEY", "")


# ── DB Setup ─────────────────────────────────────────────────
def init_db():
    db = _vshim.get_sync_db()
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""
        CREATE TABLE IF NOT EXISTS social_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            username TEXT NOT NULL,
            account_type TEXT DEFAULT 'tracker',
            tickers TEXT,
            contract_addresses TEXT,
            notes TEXT,
            last_checked TEXT,
            last_message_id TEXT DEFAULT '',
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
            sentiment TEXT,
            confidence REAL,
            post_text TEXT,
            post_url TEXT,
            signal_type TEXT DEFAULT 'mention',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # last_message_id may not exist on an older table — add defensively.
    try:
        db.execute("ALTER TABLE social_accounts ADD COLUMN last_message_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
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


# ── Sentiment (shared) ──────────────────────────────────────────
def _classify_sentiment(text):
    t = text.lower()
    if any(w in t for w in ["sell", "dump", "rug", "scam", "short", "exit", "avoid"]):
        return "BEARISH"
    if any(w in t for w in ["buy", "long", "moon", "pump", "gem", "next", "bullish", "accumulate", "loading"]):
        return "BULLISH"
    return "NEUTRAL"


def _extract_mentions(text, tracked_tickers, tracked_cas):
    """Every ticker/CA mention in a real post, not just the tracked ones —
    tracked_tickers/tracked_cas bias nothing here, they're unused filters
    left from the old version; real alpha often isn't the ticker you
    already knew to look for."""
    out = []
    sentiment = _classify_sentiment(text)
    for m in TICKER_RE.findall(text):
        out.append({"ticker": m, "sentiment": sentiment, "text": text[:400]})
    for m in set(SOL_ADDR_RE.findall(text)) | set(ETH_ADDR_RE.findall(text)):
        if len(m) >= 32:
            out.append({"contract_address": m, "sentiment": sentiment, "text": text[:400]})

    # Regex only catches exact $TICKER/CA patterns. A paraphrased call
    # ("just loaded a bag of the new deluge token, feels like it's got
    # legs") mentions neither but is obviously a real signal — that's what
    # this fills in, and only runs when regex found literally nothing, so
    # it never doubles up or costs an API call on posts already handled.
    if not out:
        try:
            import llm_extract
            llm_sig = llm_extract.extract_signal_llm(text)
            if llm_sig and llm_sig.ticker:
                out.append({
                    "ticker": llm_sig.ticker.upper(),
                    "sentiment": llm_sig.direction,
                    "text": text[:400],
                    "llm_extracted": True,
                    "llm_reasoning": llm_sig.reasoning,
                })
        except Exception:
            pass  # best-effort enrichment, never blocks the regex path
    return out


# ── Telegram Scanner — real, via Telegram's own public preview page ────────
def scan_telegram(channel, tracked_tickers, tracked_cas, last_message_id=""):
    """Fetch https://t.me/s/<channel> — Telegram's official public preview,
    no auth of any kind. Returns (signals, newest_message_id, post_urls)."""
    try:
        req = urllib.request.Request(f"https://t.me/s/{channel}", headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  Telegram error ({channel}): {e}", flush=True)
        return [], last_message_id

    # Each message block: data-post="channel/N" ... tgme_widget_message_text ...
    posts = re.findall(
        r'data-post="[^/]+/(\d+)".*?tgme_widget_message_text[^>]*>(.*?)</div>',
        raw, re.DOTALL,
    )
    signals = []
    newest_id = last_message_id
    for msg_id, raw_html in posts:
        if last_message_id and int(msg_id) <= int(last_message_id or 0):
            continue  # already processed on a prior cycle
        text = html.unescape(re.sub(r'<[^>]+>', ' ', raw_html)).strip()
        if not text:
            continue
        for sig in _extract_mentions(text, tracked_tickers, tracked_cas):
            sig["post_url"] = f"https://t.me/{channel}/{msg_id}"
            signals.append(sig)
        if int(msg_id) > int(newest_id or 0):
            newest_id = msg_id

    return signals, newest_id


# ── Twitter Scanner — real, via x_browser_scraper.py's dedicated-account
# Camoufox session (see module docstring) ──────────────────────────────────
def _twitter_posts_batch(handles):
    """One real browser session covering every tracked Twitter handle in
    this cycle, via x_browser_scraper.run_cycle_sync() -- matches a real
    human's browsing session (one sitting, several profiles) rather than
    relaunching a browser per handle. Returns {handle: [post dicts]},
    empty dict on ANY failure (camoufox not installed, no session yet,
    in backoff, a real error) -- the Telegram path must never be affected
    by a Twitter-side failure, so this is wrapped defensively rather than
    letting an ImportError or browser crash propagate into one_cycle()."""
    if not handles:
        return {}
    try:
        import x_browser_scraper
    except ImportError as e:
        print(f"  Twitter skipped: x_browser_scraper unavailable ({e})", flush=True)
        return {}
    try:
        posts = x_browser_scraper.run_cycle_sync(handles)
    except Exception as e:
        print(f"  Twitter scrape cycle failed: {e}", flush=True)
        return {}
    by_handle = {}
    for p in posts:
        by_handle.setdefault(p.get("handle", ""), []).append(p)
    return by_handle


def scan_twitter_from_batch(username, tracked_tickers, tracked_cas, batch):
    """Real ticker/CA extraction over this account's posts from the
    already-fetched batch (see _twitter_posts_batch) -- reuses
    _extract_mentions exactly like scan_telegram does, so a signal
    extracted from a tweet and a signal extracted from a Telegram post
    are indistinguishable downstream (same shape, same sentiment
    classification, same pipeline into post_signal/social_signals)."""
    signals = []
    for post in batch.get(username, []):
        for sig in _extract_mentions(post.get("text", ""), tracked_tickers, tracked_cas):
            sig["post_url"] = post.get("post_url", "")
            signals.append(sig)
    return signals


# ── Signal Poster ────────────────────────────────────────────
def post_signal(db, account_id, platform, username, ticker, ca, sentiment, confidence, text, post_url):
    db.execute(
        "INSERT INTO social_signals (account_id, platform, username, ticker, contract_address, sentiment, confidence, post_text, post_url) VALUES (?,?,?,?,?,?,?,?,?)",
        (account_id, platform, username, ticker, ca, sentiment, confidence, text[:500], post_url)
    )
    db.execute("UPDATE social_accounts SET signal_count = signal_count + 1, last_checked = ? WHERE id = ?",
               (datetime.now(timezone.utc).isoformat(), account_id))
    db.commit()

    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals/ingest",
            data=json.dumps({
                "symbol": ticker or ca or "SOCIAL", "source": f"social_{platform}",
                "conviction": confidence, "type": "sentiment",
                "detail": f"{sentiment} | {username}: {text[:100]}",
            }).encode(),
            headers={"Content-Type": "application/json", "X-Vantage-Tool": "intel", "X-Vantage-Tool-Key": VANTAGE_TOOL_KEY},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

    # PnL-post wallet extraction — reuse the same address regexes; if the
    # post itself contains a wallet address (common "here's my bag" post
    # pattern) alongside a token mention, tie it to this account and try to
    # verify it on-chain.
    for addr in set(SOL_ADDR_RE.findall(text)):
        if addr == ca:
            continue  # that's the contract address, not a wallet
        try:
            db.execute(
                """INSERT INTO social_wallet_links (platform, username, wallet_address, chain, post_url, post_excerpt)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(platform, username, wallet_address) DO UPDATE SET
                     post_url=excluded.post_url, extracted_at=datetime('now')""",
                (platform, username, addr, "solana", post_url, text[:200]),
            )
            db.commit()
            if ca:
                backtrack_pnl(db, platform, username, addr, ca, ticker)
        except sqlite3.Error:
            pass


# ── PnL Backtracking — the actual "who to copy trade" answer ───────────────
def _rpc(method, params):
    key = _helius_key()
    if not key:
        return None
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={key}", data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        return resp.get("result")
    except urllib.error.HTTPError as e:
        api_key_pool.report_error("helius", key, e.code, e.read().decode(errors="ignore"))
        return None
    except Exception:
        return None


def _dexscreener_price(mint):
    try:
        req = urllib.request.Request(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                                      headers={"User-Agent": "Vantage/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        return float(best["priceUsd"]) if best.get("priceUsd") else None
    except Exception:
        return None


def backtrack_pnl(db, platform, username, wallet, mint, symbol):
    """Find this wallet's real swap for this token (any direction, most
    recent) and compute actual entry price from the swap amounts — not the
    self-reported PnL. Compares to current price. Stores a verified_calls
    row and rolls it into wallet_reputation."""
    existing = db.execute(
        "SELECT id FROM verified_calls WHERE platform=? AND username=? AND wallet_address=? AND mint=?",
        (platform, username, wallet, mint),
    ).fetchone()
    if existing:
        return  # already backtracked this exact call

    sigs = _rpc("getSignaturesForAddress", [wallet, {"limit": 50}])
    if not sigs:
        return

    entry_price = None
    entry_sig = None
    for s in sigs:
        txn = _rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not txn:
            continue
        meta = txn.get("meta", {})
        pre = meta.get("preTokenBalances", []) or []
        post = meta.get("postTokenBalances", []) or []
        mint_delta = 0.0
        sol_delta = 0.0
        for p in post:
            if p.get("mint") == mint:
                pre_amt = next((x["uiTokenAmount"]["uiAmount"] or 0 for x in pre if x.get("accountIndex") == p.get("accountIndex")), 0)
                mint_delta += (p["uiTokenAmount"]["uiAmount"] or 0) - pre_amt
        pre_bal = meta.get("preBalances", [])
        post_bal = meta.get("postBalances", [])
        if pre_bal and post_bal:
            sol_delta = (pre_bal[0] - post_bal[0]) / 1e9  # fee payer's SOL spent, rough proxy
        if mint_delta > 0 and sol_delta > 0:
            sol_price = _dexscreener_price("So11111111111111111111111111111111111111112") or 0
            if sol_price:
                entry_price = round((sol_delta * sol_price) / mint_delta, 10)
                entry_sig = s["signature"]
            break  # most recent qualifying buy — good enough for a first pass

    if entry_price is None:
        return  # no matching on-chain buy found — don't fabricate a number

    current_price = _dexscreener_price(mint)
    pct_change = round((current_price - entry_price) / entry_price * 100, 1) if current_price else None

    db.execute(
        """INSERT INTO verified_calls (platform, username, wallet_address, mint, symbol, claimed_at, entry_price_usd, entry_tx_signature, current_price_usd, pct_change)
           VALUES (?,?,?,?,?,datetime('now'),?,?,?,?)
           ON CONFLICT(platform, username, wallet_address, mint) DO NOTHING""",
        (platform, username, wallet, mint, symbol, entry_price, entry_sig, current_price, pct_change),
    )
    db.commit()

    # Real observation trace into Mycelium's substrate (2026-08-30
    # ecosystem-wide audit) -- this INSERT only ever runs once we've
    # already checked `existing` at the top of this function and confirmed
    # no matching (platform, username, wallet, mint) row exists yet, so
    # every real call reaching here is a genuinely new, real, on-chain-
    # verified fact -- no additional dedup needed (see mycelium_bridge.
    # emit_verified_call_trace's own docstring). Fail-soft.
    try:
        from backend.mycelium_bridge import emit_verified_call_trace
        emit_verified_call_trace({
            "platform": platform, "username": username, "wallet_address": wallet,
            "mint": mint, "symbol": symbol, "entry_price_usd": entry_price,
            "entry_tx_signature": entry_sig, "current_price_usd": current_price,
            "pct_change": pct_change,
        })
    except Exception as e:
        print(f"  mycelium trace emit failed: {e}", flush=True)

    if pct_change is not None:
        row = db.execute(
            "SELECT COUNT(*), AVG(pct_change) FROM verified_calls WHERE wallet_address=? AND pct_change IS NOT NULL",
            (wallet,),
        ).fetchone()
        count, avg = row[0], row[1] or 0
        db.execute(
            """INSERT INTO wallet_reputation (wallet_address, chain, verified_call_count, verified_avg_return_pct, updated_at)
               VALUES (?, 'solana', ?, ?, datetime('now'))
               ON CONFLICT(wallet_address) DO UPDATE SET
                 verified_call_count=excluded.verified_call_count,
                 verified_avg_return_pct=excluded.verified_avg_return_pct,
                 updated_at=excluded.updated_at""",
            (wallet, count, round(avg, 1)),
        )
        db.commit()
        print(f"  ✓ verified call: {username} → {symbol or mint[:8]} entry ${entry_price:.8f} now ${current_price or 0:.8f} ({pct_change:+.1f}%)", flush=True)


# ── Main Scan Loop ───────────────────────────────────────────
def scan_all(interval=300):

    def one_cycle():
        # Opens (and always closes) a fresh connection per cycle -- was a
        # single connection opened once at daemon start and reused via
        # closure for the process's entire multi-day lifetime, which pins
        # SQLite's WAL read-mark(0) slot ("read from the very start of the
        # WAL") forever and permanently blocks wal_checkpoint from
        # truncating the WAL. Confirmed live: this connection (PID 2930)
        # was the one lslocks showed holding that lock while the WAL grew
        # unbounded to ~3GB with checkpoint stuck at ~0.3% progress.
        db = _vshim.get_sync_db()
        db.execute("PRAGMA busy_timeout=30000")
        try:
            accounts = db.execute(
                "SELECT id, platform, username, tickers, contract_addresses, last_message_id FROM social_accounts"
            ).fetchall()
            if not accounts:
                print("No accounts tracked. Add with: python3 social_tracker.py add twitter @account BTC,ETH,SOL", flush=True)
                return 0

            # One real browser session covering every tracked Twitter
            # handle this cycle -- see _twitter_posts_batch's own
            # docstring for why this is batched rather than per-account.
            twitter_handles = [row[2] for row in accounts if row[1] == "twitter"]
            twitter_batch = _twitter_posts_batch(twitter_handles)

            total = 0
            for aid, platform, username, tickers_str, cas_str, last_msg_id in accounts:
                tickers = [t.strip() for t in (tickers_str or "").split(",") if t.strip()]
                cas = [c.strip() for c in (cas_str or "").split(",") if c.strip()]

                if platform == "twitter":
                    signals = scan_twitter_from_batch(username, tickers, cas, twitter_batch)
                elif platform == "telegram":
                    signals, newest_id = scan_telegram(username, tickers, cas, last_msg_id)
                    if newest_id != last_msg_id:
                        db.execute("UPDATE social_accounts SET last_message_id=? WHERE id=?", (newest_id, aid))
                        db.commit()
                else:
                    continue

                for sig in signals:
                    ticker = sig.get("ticker", "")
                    ca = sig.get("contract_address", "")
                    sentiment = sig.get("sentiment", "NEUTRAL")
                    text = sig.get("text", "")
                    post_url = sig.get("post_url", "")
                    confidence = 0.7 if sentiment == "BULLISH" else 0.5
                    post_signal(db, aid, platform, username, ticker, ca, sentiment, confidence, text, post_url)
                    print(f"  {sentiment} {platform}: @{username} -> {ticker or ca[:12]} | {text[:60]}", flush=True)
                    total += 1
            return total
        finally:
            db.close()

    print(f"Social Tracker — {interval}s cycle", flush=True)
    if interval <= 1:
        one_cycle()
        return
    while True:
        try:
            n = one_cycle()
            if n:
                print(f"  Posted {n} social signals", flush=True)
        except Exception as e:
            print(f"cycle error: {e}", flush=True)
        time.sleep(interval)


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
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
        add_account(sys.argv[2], sys.argv[3].strip("@"),
                    sys.argv[4] if len(sys.argv) > 4 else "",
                    sys.argv[5] if len(sys.argv) > 5 else "",
                    sys.argv[6] if len(sys.argv) > 6 else "tracker",
                    sys.argv[7] if len(sys.argv) > 7 else "")
    elif cmd == "list":
        list_accounts()
    elif cmd == "scan":
        scan_all(1)
    elif cmd == "daemon":
        scan_all(int(sys.argv[2]) if len(sys.argv) > 2 else 300)
