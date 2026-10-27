"""
Token early-buyer backtracker.

Given a token mint, fetches the earliest buyers on Solana via Helius:
  1. Paginate getSignaturesForAddress(mint) to find the oldest transactions
  2. For each, parse token transfers to identify the buyer wallet
  3. Walk the buyer's funding source (who sent them SOL before their first buy)
  4. Store results in token_early_buyers (rank, SOL spent, funder, timestamp)
  5. Inject into token_wallet_roles (role='first_buyer') and wallet_edges
     (funder→buyer edge) so they appear in the Galaxy graph with correct tags

Dry-run mode: set dry_run=True to return mock data without any Helius calls.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Optional

import aiosqlite
import httpx

from backend.db import get_db

logger = logging.getLogger(__name__)

# Token accounts / program addresses that are never early buyers
_SOL_PROGRAMS = frozenset({
    "So11111111111111111111111111111111111111112",  # secret-scan:allow
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "11111111111111111111111111111111",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1Mm",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
})

_EARLY_BUYERS_DDL = """
CREATE TABLE IF NOT EXISTS token_early_buyers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mint            TEXT NOT NULL,
    wallet_address  TEXT NOT NULL,
    entry_rank      INTEGER,
    entry_ts        INTEGER,
    sol_spent       REAL DEFAULT 0.0,
    token_amount    REAL DEFAULT 0.0,
    tx_signature    TEXT,
    funded_by       TEXT,
    is_major        INTEGER DEFAULT 0,
    discovered_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(mint, wallet_address)
)
"""
_EARLY_BUYERS_IDX = "CREATE INDEX IF NOT EXISTS idx_early_buyers_mint ON token_early_buyers(mint)"


async def _ensure_tables(db: aiosqlite.Connection) -> None:
    await db.execute(_EARLY_BUYERS_DDL)
    await db.execute(_EARLY_BUYERS_IDX)
    await db.commit()


def _helius_key() -> str:
    try:
        import api_key_pool
        k = api_key_pool.get_key("helius", "early_buyer")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("HELIUS_API_KEY", "")


async def _rpc(client: httpx.AsyncClient, key: str, method: str, params: list) -> Optional[dict]:
    try:
        r = await client.post(
            f"https://mainnet.helius-rpc.com/?api-key={key}",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Content-Type": "application/json"},
            timeout=18,
        )
        if r.status_code == 200:
            return r.json().get("result")
    except Exception as e:
        logger.debug("Helius RPC %s failed: %s", method, e)
    return None


async def _paginate_signatures(
    client: httpx.AsyncClient, key: str, address: str, max_total: int = 500
) -> list:
    """Collect up to max_total signatures for address, returning ALL of them
    (newest-first as Helius delivers them) so the caller can slice the oldest."""
    all_sigs: list = []
    before: Optional[str] = None

    while len(all_sigs) < max_total:
        params: dict = {"limit": 100}
        if before:
            params["before"] = before
        result = await _rpc(client, key, "getSignaturesForAddress", [address, params])
        if not result:
            break
        all_sigs.extend(result)
        if len(result) < 100:
            break  # last page
        before = result[-1]["signature"]

    return all_sigs


async def _parse_buyer_from_tx(
    client: httpx.AsyncClient, key: str, sig: str, mint: str
) -> Optional[dict]:
    """Fetch one transaction and extract the wallet that bought `mint`."""
    txn = await _rpc(client, key, "getTransaction", [
        sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
    ])
    if not txn:
        return None

    meta = txn.get("meta") or {}
    ts = txn.get("blockTime", 0) or 0
    pre_bals = meta.get("preTokenBalances") or []
    post_bals = meta.get("postTokenBalances") or []
    pre_sol = meta.get("preBalances") or []
    post_sol = meta.get("postBalances") or []

    # Find wallet that received this mint (positive delta in post vs pre)
    pre_map: dict = {}
    for p in pre_bals:
        if p.get("mint") == mint:
            pre_map[p.get("accountIndex", -1)] = float(
                (p.get("uiTokenAmount") or {}).get("uiAmount") or 0
            )

    buyer_wallet: Optional[str] = None
    token_received = 0.0
    for p in post_bals:
        if p.get("mint") != mint:
            continue
        idx = p.get("accountIndex", -1)
        post_amt = float((p.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        pre_amt = pre_map.get(idx, 0.0)
        delta = post_amt - pre_amt
        if delta > 0:
            # The owner of this token account is the buyer
            owner = p.get("owner")
            if owner and owner not in _EXCLUDED:
                buyer_wallet = owner
                token_received = delta
                break

    if not buyer_wallet:
        return None

    # Estimate SOL spent: account index 0 is usually the fee payer / buyer
    sol_spent = 0.0
    if pre_sol and post_sol and len(pre_sol) > 0 and len(post_sol) > 0:
        sol_spent = max(0.0, (pre_sol[0] - post_sol[0]) / 1e9)

    return {
        "wallet_address": buyer_wallet,
        "entry_ts": ts,
        "sol_spent": round(sol_spent, 6),
        "token_amount": round(token_received, 6),
        "tx_signature": sig,
    }


async def _find_funder(
    client: httpx.AsyncClient, key: str, wallet: str
) -> Optional[str]:
    """Find the wallet that funded `wallet` (first SOL transfer to it)."""
    sigs = await _rpc(client, key, "getSignaturesForAddress", [wallet, {"limit": 50}])
    if not sigs:
        return None

    # Oldest sig is last in the list
    oldest_sig = sigs[-1]["signature"]
    txn = await _rpc(client, key, "getTransaction", [
        oldest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
    ])
    if not txn:
        return None

    # Look for an SOL transfer TO this wallet
    meta = txn.get("meta") or {}
    pre_bals = meta.get("preBalances") or []
    post_bals = meta.get("postBalances") or []

    # Account keys in the transaction
    msg = txn.get("transaction", {}).get("message", {})
    account_keys = []
    for ak in msg.get("accountKeys") or []:
        if isinstance(ak, dict):
            account_keys.append(ak.get("pubkey", ""))
        else:
            account_keys.append(str(ak))

    for idx, (pre, post) in enumerate(zip(pre_bals, post_bals)):
        if idx >= len(account_keys):
            break
        addr = account_keys[idx]
        if addr == wallet and (post - pre) > 10_000_000:  # > 0.01 SOL received
            # The sender is usually account index 0 (fee payer / sender)
            if len(account_keys) > 0 and account_keys[0] != wallet:
                funder = account_keys[0]
                if funder not in _SOL_PROGRAMS:
                    return funder
    return None


async def backtrack_early_buyers(
    mint: str,
    max_buyers: int = 50,
    dry_run: bool = False,
) -> dict:
    """
    Walk the earliest transactions for `mint` and identify first buyers.

    Returns:
        {mint, buyers: [...], new: int, total: int, cached: bool}

    Each buyer entry:
        {wallet_address, entry_rank, entry_ts, sol_spent, token_amount,
         tx_signature, funded_by, is_major}
    """
    if dry_run:
        return _dry_run_result(mint, max_buyers)

    key = _helius_key()
    if not key:
        return {"mint": mint, "error": "no_helius_key", "buyers": [], "new": 0, "total": 0}

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        await _ensure_tables(db)

        # Return cached if already backtracked
        cached = [dict(r) for r in await (await db.execute(
            "SELECT * FROM token_early_buyers WHERE mint=? ORDER BY entry_rank ASC",
            (mint,),
        )).fetchall()]
        if cached:
            return {
                "mint": mint, "buyers": cached,
                "new": 0, "total": len(cached), "cached": True,
            }

    # ── Helius lookup ────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=25) as client:
        # Collect up to 500 signatures; oldest buyers are at the END of list
        all_sigs = await _paginate_signatures(client, key, mint, max_total=500)
        if not all_sigs:
            return {"mint": mint, "error": "no_signatures", "buyers": [], "new": 0, "total": 0}

        # Oldest N signatures (these are at the tail since Helius returns newest-first)
        oldest_sigs = all_sigs[-(min(100, len(all_sigs))):]  # up to 100 oldest

        # Parse buyer from each sig concurrently (batched to avoid hammering)
        BATCH = 10
        raw_buyers: list[dict] = []
        for i in range(0, len(oldest_sigs), BATCH):
            batch_sigs = oldest_sigs[i:i + BATCH]
            results = await asyncio.gather(*[
                _parse_buyer_from_tx(client, key, s["signature"], mint)
                for s in batch_sigs
            ], return_exceptions=True)
            for r in results:
                if isinstance(r, dict):
                    raw_buyers.append(r)
            if len(raw_buyers) >= max_buyers:
                break

        # Deduplicate wallets, keep earliest entry per wallet
        seen: dict[str, dict] = {}
        for b in raw_buyers:
            w = b["wallet_address"]
            if w not in seen or b["entry_ts"] < seen[w]["entry_ts"]:
                seen[w] = b

        # Sort by entry timestamp, assign ranks
        ranked = sorted(seen.values(), key=lambda b: b["entry_ts"])
        for i, b in enumerate(ranked, 1):
            b["entry_rank"] = i

        # Mark major early buyers: top 10 by SOL spent OR first 5 by rank
        top_by_sol = sorted(ranked, key=lambda b: -b["sol_spent"])[:10]
        major_set = {b["wallet_address"] for b in top_by_sol[:10]} | \
                    {b["wallet_address"] for b in ranked[:5]}
        for b in ranked:
            b["is_major"] = 1 if b["wallet_address"] in major_set else 0

        # Find funders for major early buyers (concurrently)
        major_buyers = [b for b in ranked if b["is_major"]]
        funder_results = await asyncio.gather(*[
            _find_funder(client, key, b["wallet_address"]) for b in major_buyers
        ], return_exceptions=True)
        for b, funder in zip(major_buyers, funder_results):
            b["funded_by"] = funder if isinstance(funder, str) else None

    # ── Persist ──────────────────────────────────────────────────────────
    new_count = 0
    async with get_db() as db:
        await _ensure_tables(db)

        for b in ranked[:max_buyers]:
            try:
                await db.execute(
                    """INSERT INTO token_early_buyers
                       (mint, wallet_address, entry_rank, entry_ts, sol_spent,
                        token_amount, tx_signature, funded_by, is_major)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(mint, wallet_address) DO NOTHING""",
                    (mint, b["wallet_address"], b["entry_rank"], b["entry_ts"],
                     b["sol_spent"], b["token_amount"], b["tx_signature"],
                     b.get("funded_by"), b["is_major"]),
                )
                new_count += 1
            except aiosqlite.Error:
                pass

        # Inject into token_wallet_roles so moneyflow picks them up
        for b in ranked[:max_buyers]:
            try:
                await db.execute(
                    """INSERT INTO token_wallet_roles
                       (mint, symbol, wallet_address, role, rank, metric, metric_label,
                        discovered_at)
                       VALUES (?,?,?,'first_buyer',?,?,?,datetime('now'))
                       ON CONFLICT DO NOTHING""",
                    (mint, mint[:8], b["wallet_address"], b["entry_rank"],
                     b["sol_spent"], f"Entry rank #{b['entry_rank']}"),
                )
            except aiosqlite.Error:
                pass

        # Inject funder→buyer wallet_edges
        for b in ranked[:max_buyers]:
            funder = b.get("funded_by")
            if not funder:
                continue
            try:
                await db.execute(
                    """INSERT INTO wallet_edges
                       (chain, address_a, address_b, role, tx_count, total_value,
                        first_seen, last_seen)
                       VALUES ('solana',?,?,'funded',1,?,datetime('now'),datetime('now'))
                       ON CONFLICT(chain, address_a, address_b, role) DO UPDATE SET
                         tx_count=tx_count+1, last_seen=datetime('now')""",
                    (funder, b["wallet_address"], b["sol_spent"]),
                )
            except aiosqlite.Error:
                pass

        await db.commit()

    return {
        "mint": mint,
        "buyers": ranked[:max_buyers],
        "new": new_count,
        "total": len(ranked),
        "cached": False,
    }


def _dry_run_result(mint: str, max_buyers: int) -> dict:
    """Return plausible mock data without any network calls (for testing)."""
    import random, string

    def rand_wallet() -> str:
        chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        return "".join(random.choices(chars, k=44))

    now = int(time.time())
    buyers = []
    funder = rand_wallet()
    for rank in range(1, min(max_buyers, 10) + 1):
        buyers.append({
            "wallet_address": rand_wallet(),
            "entry_rank": rank,
            "entry_ts": now - (3600 * (10 - rank)),
            "sol_spent": round(random.uniform(0.1, 5.0), 4),
            "token_amount": round(random.uniform(1_000_000, 50_000_000), 2),
            "tx_signature": "DRY" + rand_wallet()[:40],
            "funded_by": funder if rank <= 3 else None,
            "is_major": 1 if rank <= 5 else 0,
        })

    return {
        "mint": mint,
        "buyers": buyers,
        "new": len(buyers),
        "total": len(buyers),
        "cached": False,
        "dry_run": True,
    }


async def get_cached_early_buyers(mint: str) -> list[dict]:
    """Fast read path: return already-stored early buyers for a mint."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        try:
            rows = await (await db.execute(
                "SELECT * FROM token_early_buyers WHERE mint=? ORDER BY entry_rank ASC",
                (mint,),
            )).fetchall()
            return [dict(r) for r in rows]
        except aiosqlite.Error:
            return []
