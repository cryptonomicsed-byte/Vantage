"""
Social-to-chain linker.

Given a Twitter/X handle or Telegram channel, discovers Solana wallets
linked to that identity via:
  1. Existing social_signals rows (CA mentions already extracted by social_tracker)
  2. social_wallet_links rows (PnL-post wallet claims from social_tracker)
  3. A fresh lightweight Telegram page scan (on-demand, platform=telegram + refresh=True)

Discovered wallets are stored in social_chain_links (handle→wallet mapping,
persisted across calls) and injected into wallet_edges so they appear
immediately in the Galaxy graph as nodes linked to the social handle.
"""
import re
import html
import asyncio
import logging
import urllib.request
import aiosqlite

from backend.db import get_db

logger = logging.getLogger(__name__)

SOL_ADDR_RE = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')

# Program / burn addresses that are never wallets
_SOL_PROGRAMS = frozenset({
    "So11111111111111111111111111111111111111112",  # secret-scan:allow
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "11111111111111111111111111111111",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1Mm",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
})

_DDL = """
CREATE TABLE IF NOT EXISTS social_chain_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    handle      TEXT NOT NULL,
    platform    TEXT NOT NULL DEFAULT 'twitter',
    wallet_address TEXT NOT NULL,
    chain       TEXT DEFAULT 'solana',
    discovery_method TEXT DEFAULT 'post_mention',
    source_post_url  TEXT,
    source_text      TEXT,
    mint             TEXT,
    confidence  REAL DEFAULT 1.0,
    created_at  TEXT DEFAULT (datetime('now')),
    refreshed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(handle, platform, wallet_address)
)
"""


async def _ensure_table(db: aiosqlite.Connection) -> None:
    await db.execute(_DDL)
    await db.commit()


def _is_wallet(addr: str) -> bool:
    return len(addr) >= 32 and addr not in _SOL_PROGRAMS


async def link_handle(
    handle: str,
    platform: str = "twitter",
    refresh: bool = False,
) -> dict:
    """
    Discover wallets associated with `handle` on `platform`.

    Returns:
        {handle, platform, wallets: [str], wallet_details: [...], new: int, cached: bool}
    """
    handle = handle.lower().strip("@")

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await _ensure_table(db)

        existing = [dict(r) for r in await (await db.execute(
            "SELECT wallet_address, discovery_method, confidence, refreshed_at "
            "FROM social_chain_links WHERE handle=? AND platform=? ORDER BY confidence DESC",
            (handle, platform),
        )).fetchall()]

        if existing and not refresh:
            return {
                "handle": handle, "platform": platform,
                "wallets": [r["wallet_address"] for r in existing],
                "wallet_details": existing,
                "new": 0, "cached": True,
            }

        discovered: list[dict] = []

        # ── Source 1: social_signals CAs from this handle ────────────────
        try:
            sig_rows = [dict(r) for r in await (await db.execute(
                """SELECT contract_address, post_text, post_url FROM social_signals
                   WHERE username=? AND platform=? AND contract_address IS NOT NULL
                   ORDER BY id DESC LIMIT 300""",
                (handle, platform),
            )).fetchall()]
        except aiosqlite.Error:
            sig_rows = []

        for row in sig_rows:
            ca = (row.get("contract_address") or "").strip()
            if _is_wallet(ca):
                discovered.append({
                    "wallet_address": ca,
                    "discovery_method": "social_signal_ca",
                    "source_post_url": row.get("post_url") or "",
                    "source_text": (row.get("post_text") or "")[:200],
                    "mint": None,
                    "confidence": 0.7,
                })

        # ── Source 2: social_wallet_links (PnL-post explicit wallet claims) ──
        try:
            link_rows = [dict(r) for r in await (await db.execute(
                """SELECT wallet_address, post_url, post_excerpt FROM social_wallet_links
                   WHERE username=? AND platform=? ORDER BY extracted_at DESC LIMIT 100""",
                (handle, platform),
            )).fetchall()]
        except aiosqlite.Error:
            link_rows = []

        for row in link_rows:
            w = (row.get("wallet_address") or "").strip()
            if _is_wallet(w):
                discovered.append({
                    "wallet_address": w,
                    "discovery_method": "pnl_post_claim",
                    "source_post_url": row.get("post_url") or "",
                    "source_text": (row.get("post_excerpt") or "")[:200],
                    "mint": None,
                    "confidence": 0.9,
                })

        # ── Source 3: fresh Telegram scan (on-demand, fail-soft) ─────────
        if platform == "telegram" and refresh:
            fresh = await asyncio.to_thread(_scan_telegram_fresh, handle)
            discovered.extend(fresh)

        # Deduplicate: keep highest confidence per wallet
        by_wallet: dict[str, dict] = {}
        for d in discovered:
            w = d["wallet_address"]
            if w not in by_wallet or d["confidence"] > by_wallet[w]["confidence"]:
                by_wallet[w] = d

        discovered = list(by_wallet.values())

        # Persist to social_chain_links
        new_count = 0
        for d in discovered:
            try:
                await db.execute(
                    """INSERT INTO social_chain_links
                       (handle, platform, wallet_address, chain, discovery_method,
                        source_post_url, source_text, mint, confidence, refreshed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
                       ON CONFLICT(handle, platform, wallet_address) DO UPDATE SET
                         confidence=MAX(excluded.confidence, social_chain_links.confidence),
                         refreshed_at=datetime('now')""",
                    (handle, platform, d["wallet_address"], "solana",
                     d["discovery_method"], d["source_post_url"], d["source_text"],
                     d.get("mint"), d["confidence"]),
                )
                new_count += 1
            except aiosqlite.Error:
                pass

        # Inject into wallet_edges so the social→wallet link appears in the graph
        for d in discovered:
            try:
                await db.execute(
                    """INSERT INTO wallet_edges
                       (chain, address_a, address_b, role, tx_count, total_value,
                        first_seen, last_seen)
                       VALUES ('solana',?,?,'social_link',1,0,datetime('now'),datetime('now'))
                       ON CONFLICT(chain, address_a, address_b, role) DO UPDATE SET
                         last_seen=datetime('now'), tx_count=tx_count+1""",
                    (handle, d["wallet_address"]),
                )
            except aiosqlite.Error:
                pass

        await db.commit()

        all_wallets = list({
            **{r["wallet_address"]: r for r in existing},
            **{d["wallet_address"]: d for d in discovered},
        }.values())

        return {
            "handle": handle,
            "platform": platform,
            "wallets": [w["wallet_address"] for w in all_wallets],
            "wallet_details": all_wallets,
            "new": new_count,
            "cached": len(existing) > 0 and not refresh,
        }


def _scan_telegram_fresh(channel: str) -> list:
    """Lightweight one-shot Telegram public page scan. Runs in a thread."""
    found = []
    try:
        req = urllib.request.Request(
            f"https://t.me/s/{channel}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Vantage/1.0)"},
        )
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning("Telegram fresh scan failed for %s: %s", channel, e)
        return []

    posts = re.findall(
        r'data-post="[^/]+/(\d+)".*?tgme_widget_message_text[^>]*>(.*?)</div>',
        raw, re.DOTALL,
    )
    for msg_id, raw_html in posts:
        text = html.unescape(re.sub(r'<[^>]+>', ' ', raw_html)).strip()
        post_url = f"https://t.me/{channel}/{msg_id}"
        for addr in set(SOL_ADDR_RE.findall(text)):
            if _is_wallet(addr):
                found.append({
                    "wallet_address": addr,
                    "discovery_method": "telegram_fresh_scan",
                    "source_post_url": post_url,
                    "source_text": text[:200],
                    "mint": None,
                    "confidence": 0.65,
                })
    return found


async def get_linked_wallets(handle: str, platform: str = "twitter") -> list[str]:
    """Fast read path: return cached wallet addresses for a handle."""
    handle = handle.lower().strip("@")
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT wallet_address FROM social_chain_links "
            "WHERE handle=? AND platform=? ORDER BY confidence DESC",
            (handle, platform),
        )).fetchall()
        return [r[0] for r in rows]
