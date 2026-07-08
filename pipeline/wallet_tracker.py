#!/usr/bin/env python3
"""
Live Wallet Trade Tracker — auto-ingests real on-chain trades + data.

Polls the Helius Enhanced Transactions API for each tracked Solana wallet,
parses swaps/transfers, and writes them into the Vantage DB (wallet_trades).
New trades (seen after startup) are also posted to the Vantage feed so the
platform reflects live wallet activity automatically. Complements
wallet_balance_updater.py (which tracks balances) — this one tracks trades.

No private keys are used or stored: this is read-only chain data.

Env (see .env.example):
  HELIUS_API_KEY      Helius key (Enhanced Transactions API)
  VANTAGE_URL         default http://localhost:8001
  VANTAGE_KEY         agent key for posting to the feed (optional; skip feed if unset)
  DB_PATH             default /opt/ares/Vantage/data/vantage.db
  TRADE_WALLET        fallback wallet if trading_wallets table is empty
  WALLET_POLL_SECONDS default 60
"""
import os, sys, json, time, sqlite3, urllib.request
from datetime import datetime, timezone

# Optional: load a local .env for manual runs (systemd already injects env).
def _load_dotenv():
    for path in (os.environ.get("ENV_FILE"), ".env", "/opt/ares/Vantage/.env"):
        if path and os.path.isfile(path):
            for line in open(path):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
            return
_load_dotenv()

HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "")
VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
DB_PATH = os.environ.get("DB_PATH", "/opt/ares/Vantage/data/vantage.db")
TRADE_WALLET = os.environ.get("TRADE_WALLET", "")
INTERVAL = int(os.environ.get("WALLET_POLL_SECONDS", "60"))

# Only post trades to the feed if they happened after the tracker started,
# so a first run backfills history into the DB without flooding the feed.
START_TS = int(time.time())


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS wallet_trades (
            signature    TEXT PRIMARY KEY,
            wallet       TEXT,
            timestamp    INTEGER,
            ts_iso       TEXT,
            type         TEXT,
            source       TEXT,
            description  TEXT,
            fee_sol      REAL,
            sol_change   REAL,
            token_mint   TEXT,
            token_amount REAL,
            raw          TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_wallet_trades_wallet ON wallet_trades(wallet)")
    db.commit()
    return db


def tracked_wallets(db):
    """Solana wallets from trading_wallets; fall back to TRADE_WALLET env."""
    wallets = []
    try:
        rows = db.execute(
            "SELECT address FROM trading_wallets WHERE chain = 'solana' AND address IS NOT NULL"
        ).fetchall()
        wallets = [r[0] for r in rows if r[0]]
    except sqlite3.Error:
        pass  # table may not exist yet
    if not wallets and TRADE_WALLET:
        wallets = [TRADE_WALLET]
    return wallets


def fetch_transactions(address, limit=25):
    """Recent parsed transactions for a wallet via Helius Enhanced API."""
    if not HELIUS_KEY:
        return []
    url = (f"https://api.helius.xyz/v0/addresses/{address}/transactions"
           f"?api-key={HELIUS_KEY}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "vantage-wallet-tracker"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  fetch error ({address[:8]}…): {e}", flush=True)
        return []


def parse_trade(tx, wallet):
    """Extract wallet-centric SOL delta + first token transfer from a parsed tx."""
    sol_change = 0.0
    for nt in tx.get("nativeTransfers", []) or []:
        amt = (nt.get("amount", 0) or 0) / 1e9
        if nt.get("toUserAccount") == wallet:
            sol_change += amt
        elif nt.get("fromUserAccount") == wallet:
            sol_change -= amt

    token_mint, token_amount = None, None
    for tt in tx.get("tokenTransfers", []) or []:
        if tt.get("toUserAccount") == wallet or tt.get("fromUserAccount") == wallet:
            token_mint = tt.get("mint")
            amt = tt.get("tokenAmount", 0) or 0
            try:
                amt = float(amt)
            except (TypeError, ValueError):
                amt = 0.0
            token_amount = amt if tt.get("toUserAccount") == wallet else -amt
            break

    return sol_change, token_mint, token_amount


def post_feed(title, content):
    if not VANTAGE_KEY:
        return
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/trading/signals/ingest",
            data=json.dumps({"title": title, "content": content,
                             "tags": ["wallet", "trades", "live"],
                             "status": "published", "content_type": "text"}).encode(),
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def ingest(db, wallet):
    new = 0
    for tx in fetch_transactions(wallet):
        sig = tx.get("signature")
        if not sig:
            continue
        ts = int(tx.get("timestamp", 0) or 0)
        ttype = tx.get("type", "UNKNOWN")
        source = tx.get("source", "")
        desc = tx.get("description", "")
        fee_sol = (tx.get("fee", 0) or 0) / 1e9
        sol_change, mint, tok_amt = parse_trade(tx, wallet)

        cur = db.execute(
            """INSERT OR IGNORE INTO wallet_trades
               (signature, wallet, timestamp, ts_iso, type, source, description,
                fee_sol, sol_change, token_mint, token_amount, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sig, wallet, ts,
             datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "",
             ttype, source, desc, fee_sol, sol_change, mint, tok_amt,
             json.dumps(tx)[:8000]),
        )
        if cur.rowcount:  # genuinely new row
            new += 1
            # Only announce trades that occurred after startup (skip backfill).
            if ts >= START_TS:
                sol_str = f"{sol_change:+.4f} SOL" if sol_change else ""
                tok_str = f" | token {mint[:6]}… {tok_amt:+.2f}" if mint else ""
                post_feed(
                    f"💱 {ttype} — {wallet[:6]}…{wallet[-4:]}",
                    f"{desc or ttype}\n\n{sol_str}{tok_str}\n"
                    f"https://solscan.io/tx/{sig}",
                )
                print(f"  ⚡ NEW {ttype} {sol_change:+.4f} SOL {sig[:16]}…", flush=True)
    db.commit()
    return new


def run():
    print(f"Wallet Trade Tracker — {INTERVAL}s cycle — DB {DB_PATH}", flush=True)
    if not HELIUS_KEY:
        print("  WARNING: HELIUS_API_KEY not set — set it in .env; idling.", flush=True)
    db = init_db()
    while True:
        try:
            wallets = tracked_wallets(db)
            total = sum(ingest(db, w) for w in wallets)
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{stamp}] {len(wallets)} wallet(s), {total} new trade(s)", flush=True)
        except Exception as e:
            print(f"Error: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
