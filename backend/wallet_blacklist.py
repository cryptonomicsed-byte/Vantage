"""Shared exchange blacklist — major exchange/custodian wallets add no
"follow the money" value in the graph (everything eventually routes through
them, so they become false hubs that make every wallet look connected to
every other wallet) and provide no alpha for copy-trading. Centralized here
so degen.py's smart-wallets filter, alpha.py's /api/moneyflow graph, and the
wallet_learner.py copy-trade daemon all use the exact same exclusion logic
instead of drifting apart.

Two layers:
  1. Pattern match (KNOWN_EXCHANGE_LABEL_PATTERNS) — catches any wallet
     labeled with a known exchange name regardless of how it was tagged.
  2. Explicit wallet_blacklist table — for anything the patterns miss, or
     any wallet you want excluded for a different reason (a known bot,
     a provider/router contract, etc.), addable via the API below.
"""
import sqlite3

DB_PATH = "/opt/ares/Vantage/data/vantage.db"

KNOWN_EXCHANGE_LABEL_PATTERNS = [
    "Binance", "Coinbase", "Kraken", "OKX", "Bybit", "FTX", "Alameda", "Huobi",
    "HTX", "KuCoin", "Gate.io", "MEXC", "Bitfinex", "Crypto.com", "Gemini",
    "Bitstamp", "Upbit", "Bithumb", "WhiteBIT", "BitMart", "Poloniex", "Bittrex",
    "OKEx", "Bitget", "LBank", "Bibox", "DigiFinex", "CoinEx", "Deribit",
]

_BLACKLIST_DDL = """
CREATE TABLE IF NOT EXISTS wallet_blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    chain TEXT DEFAULT 'solana',
    reason TEXT DEFAULT '',
    added_by_agent_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(chain, address)
)
"""


def is_exchange_label(label: str) -> bool:
    if not label:
        return False
    label_l = label.lower()
    return any(p.lower() in label_l for p in KNOWN_EXCHANGE_LABEL_PATTERNS)


def sql_label_exclusions(column: str = "label") -> str:
    """SQL fragment excluding rows whose `column` matches a known exchange
    pattern — AND-joined so it can be appended to a WHERE clause."""
    return " AND ".join(f"{column} NOT LIKE '%{p}%'" for p in KNOWN_EXCHANGE_LABEL_PATTERNS)


def get_blacklisted_addresses(chain: str = None) -> set:
    """Every explicitly-blacklisted address (from the wallet_blacklist table),
    optionally filtered to one chain. Cheap — call per-request, not cached,
    since the list is small and correctness matters more than shaving a
    query here."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(_BLACKLIST_DDL)
        if chain:
            rows = conn.execute("SELECT address FROM wallet_blacklist WHERE chain=?", (chain,)).fetchall()
        else:
            rows = conn.execute("SELECT address FROM wallet_blacklist").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def add_to_blacklist(address: str, chain: str = "solana", reason: str = "", agent_id: int = None):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(_BLACKLIST_DDL)
        conn.execute(
            """INSERT INTO wallet_blacklist (address, chain, reason, added_by_agent_id)
               VALUES (?,?,?,?)
               ON CONFLICT(chain, address) DO UPDATE SET reason=excluded.reason""",
            (address, chain, reason, agent_id),
        )
        conn.commit()
    finally:
        conn.close()
