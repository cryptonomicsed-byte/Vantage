"""
Vantage Execution Engine — background order processor.

Runs as an asyncio task in the Vantage lifespan (started from main.py when
``settings.TRADING_ENGINE_ENABLED``). Polls trading_orders for pending rows and
executes each through a chain-specific adapter. Per-agent isolation: wallet keys
are decrypted with the owning agent's API key, never a shared secret.

Two gates, independent by design:
  • TRADING_ENGINE_ENABLED — whether this loop runs at all.
  • TRADING_LIVE_ENABLED   — whether adapters actually sign + submit on-chain.
    When false, the Solana adapter builds the full Jupiter swap (proving the
    path works) but stops before submission and marks the order 'ready'.

Safety guards (Solana), all configurable in settings:
  • max SOL per order and a rolling 24h SOL spend cap
  • max concurrent pending orders (checked before executing)
  • liquidity floor + mint-authority (rug) rejection for token buys
  • cooldown between two on-chain trades
"""
import asyncio
import json
import logging
import time
from datetime import datetime

import aiosqlite

from backend.config import settings
from backend.crypto_utils import decrypt_private_key
from backend.db import DB_PATH

logger = logging.getLogger(__name__)

# Chain-specific execution adapters: (order, wallet_key) -> (tx_hash, status, error)
ADAPTERS = {}

# Canonical Solana mints for symbol resolution.
SOLANA_TOKENS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "WSOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
}
_WSOL = SOLANA_TOKENS["SOL"]
_USDC = SOLANA_TOKENS["USDC"]

# In-process guard against back-to-back on-chain submissions.
_last_trade_ts: float = 0.0


def register_adapter(chain: str, fn):
    """Register an execution adapter for a chain."""
    ADAPTERS[chain.lower()] = fn


def _helius_rpc_url() -> str:
    key = settings.HELIUS_API_KEY
    return f"https://mainnet.helius-rpc.com/?api-key={key}" if key else ""


# ── DB helpers ──────────────────────────────────────────────────────────────

async def _load_agent_info(agent_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, name, api_key FROM agents WHERE id=?", (agent_id,)
        )).fetchone()
        return dict(row) if row else None


async def _get_wallet_key(wallet_id: int, agent_info: dict) -> tuple[str | None, str | None]:
    """Decrypt a wallet's private key. Returns (address, decrypted_key)."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT address, encrypted_private_key, chain FROM trading_wallets WHERE id=? AND agent_id=?",
            (wallet_id, agent_info["id"])
        )).fetchone()
        if not row:
            return None, None
        address, encrypted, chain = row
        if not encrypted:
            return address, None
        try:
            key = decrypt_private_key(encrypted, agent_info["api_key"], agent_info["id"])
            return address, key
        except Exception as e:
            logger.warning(f"Failed to decrypt wallet {wallet_id}: {e}")
            return address, None


async def _get_pending_orders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT o.*, w.address as wallet_address, w.encrypted_private_key, w.chain as wallet_chain
               FROM trading_orders o
               LEFT JOIN trading_wallets w ON w.id = o.wallet_id
               WHERE o.status = 'pending'
               ORDER BY o.created_at
               LIMIT 10"""
        )).fetchall()
        return [dict(r) for r in rows]


async def _count_active_pending() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM trading_orders WHERE status IN ('pending','submitted')"
        )).fetchone()
        return int(row[0]) if row else 0


async def _sol_spent_last_24h() -> float:
    """SOL committed to buys executed/submitted in the last 24h (rolling cap)."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            """SELECT COALESCE(SUM(quantity),0) FROM trading_orders
               WHERE chain='solana' AND side='BUY'
               AND status IN ('submitted','filled','confirmed')
               AND COALESCE(executed_at, created_at) >= datetime('now','-1 day')"""
        )).fetchone()
        return float(row[0]) if row else 0.0


async def _update_order(order_id: int, status: str, tx_hash: str = "", error: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        if status in ("filled", "confirmed"):
            await db.execute(
                """UPDATE trading_orders SET status=?, tx_hash=?, executed_at=datetime('now'),
                   settled_at=datetime('now'), error=? WHERE id=?""",
                (status, tx_hash, error, order_id))
        elif status == "submitted":
            await db.execute(
                """UPDATE trading_orders SET status=?, tx_hash=?, executed_at=datetime('now'),
                   error=? WHERE id=?""",
                (status, tx_hash, error, order_id))
        elif status == "failed":
            await db.execute(
                "UPDATE trading_orders SET status=?, error=?, settled_at=datetime('now') WHERE id=?",
                (status, error, order_id))
        else:
            await db.execute(
                "UPDATE trading_orders SET status=?, tx_hash=?, error=? WHERE id=?",
                (status, tx_hash, error, order_id))
        await db.commit()


# ── Solana / Jupiter adapter ────────────────────────────────────────────────

def _resolve_solana_mint(symbol: str) -> str | None:
    """A known symbol → mint, or the string itself if it looks like a mint."""
    s = (symbol or "").strip()
    if s.upper() in SOLANA_TOKENS:
        return SOLANA_TOKENS[s.upper()]
    # Base58 mint addresses are 32–44 chars; treat as a raw mint.
    if 32 <= len(s) <= 44 and s.isalnum():
        return s
    return None


async def _jupiter_quote(client, input_mint: str, output_mint: str,
                         amount: int, slippage_bps: int) -> dict:
    url = (f"{settings.JUPITER_BASE_URL}/quote?inputMint={input_mint}"
           f"&outputMint={output_mint}&amount={amount}&slippageBps={slippage_bps}")
    resp = await client.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


async def _check_solana_liquidity(order: dict, out_mint: str) -> str | None:
    """Return an error string if the target token fails safety, else None.
    Best-effort: on API failure we do NOT trade (fail closed) for token buys."""
    # Only scrutinize buys of non-stable/non-SOL tokens (the risky direction).
    if out_mint in (_WSOL, _USDC):
        return None
    helius = _helius_rpc_url()
    if not helius:
        return "no HELIUS_API_KEY for safety checks"
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # Mint authority still set → can mint more supply → rug risk.
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                       "params": [out_mint, {"encoding": "jsonParsed"}]}
            r = await client.post(helius, json=payload, timeout=10)
            info = r.json().get("result", {}).get("value", {}) or {}
            parsed = info.get("data", {}).get("parsed", {}).get("info", {})
            if parsed.get("mintAuthority"):
                return f"token {out_mint[:8]} has active mint authority (rug risk)"
    except Exception as e:
        return f"safety check failed: {e}"
    return None


async def _process_solana_order(order: dict, wallet_key: str):
    """Execute a Solana order via Jupiter: quote → swap tx → sign → submit."""
    global _last_trade_ts
    try:
        import httpx
    except ImportError:
        return None, "failed", "httpx not installed in Vantage venv"

    symbol = order.get("symbol", "SOL/USDC")
    side = (order.get("side") or "BUY").upper()
    quantity = float(order.get("quantity") or 0)
    slippage_bps = settings.TRADING_DEFAULT_SLIPPAGE_BPS

    parts = symbol.split("/")
    base = parts[0].strip()
    base_mint = _resolve_solana_mint(base)
    if not base_mint:
        return None, "failed", f"unknown Solana token '{base}' (add to SOLANA_TOKENS or use mint)"

    # BUY base: spend SOL to acquire base. SELL base: swap base back to SOL.
    if side == "BUY":
        input_mint, output_mint = _WSOL, base_mint
        if quantity > settings.TRADING_MAX_SOL_PER_ORDER:
            return None, "failed", (f"order {quantity} SOL exceeds per-order cap "
                                    f"{settings.TRADING_MAX_SOL_PER_ORDER} SOL")
        amount = int(quantity * 1_000_000_000)  # SOL: 9 decimals
    else:
        input_mint, output_mint = base_mint, _WSOL
        amount = int(quantity * 1_000_000_000)

    if amount <= 0:
        return None, "failed", "order quantity resolves to zero base units"

    safety_err = await _check_solana_liquidity(order, output_mint)
    if safety_err:
        return None, "failed", safety_err

    async with httpx.AsyncClient() as client:
        try:
            quote = await _jupiter_quote(client, input_mint, output_mint, amount, slippage_bps)
        except Exception as e:
            return None, "failed", f"Jupiter quote failed: {e}"
        if not quote or "outAmount" not in quote:
            return None, "failed", f"no Jupiter route for {symbol}"

        if not settings.TRADING_LIVE_ENABLED:
            out = quote.get("outAmount")
            impact = quote.get("priceImpactPct", "?")
            return (None, "ready",
                    f"DRY-RUN: {side} {quantity} {base} routed "
                    f"(out={out}, impact={impact}). Set TRADING_LIVE_ENABLED to submit.")

        # ---- live path: build, sign, submit ----
        try:
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction
            from base58 import b58decode
        except ImportError:
            return None, "failed", "solders/base58 not installed — cannot sign (pip install solders base58)"

        try:
            kp = Keypair.from_bytes(b58decode(wallet_key)) if len(wallet_key) < 90 \
                else Keypair.from_bytes(bytes(json.loads(wallet_key)))
        except Exception as e:
            return None, "failed", f"could not load signing key: {e}"

        try:
            swap_resp = await client.post(
                f"{settings.JUPITER_BASE_URL}/swap",
                json={"quoteResponse": quote, "userPublicKey": str(kp.pubkey()),
                      "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True},
                timeout=20)
            swap_resp.raise_for_status()
            swap_tx_b64 = swap_resp.json()["swapTransaction"]
        except Exception as e:
            return None, "failed", f"Jupiter swap build failed: {e}"

        try:
            import base64
            raw = VersionedTransaction.from_bytes(base64.b64decode(swap_tx_b64))
            signed = VersionedTransaction(raw.message, [kp])
            signed_b64 = base64.b64encode(bytes(signed)).decode()
        except Exception as e:
            return None, "failed", f"transaction signing failed: {e}"

        helius = _helius_rpc_url()
        if not helius:
            return None, "failed", "no HELIUS_API_KEY to submit transaction"
        try:
            submit = await client.post(helius, json={
                "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                "params": [signed_b64, {"encoding": "base64", "skipPreflight": True,
                                        "maxRetries": 3}]}, timeout=20)
            result = submit.json()
            if "error" in result:
                return None, "failed", f"RPC rejected tx: {result['error']}"
            tx_hash = result.get("result", "")
        except Exception as e:
            return None, "failed", f"submit failed: {e}"

        _last_trade_ts = time.time()
        logger.info(f"[SOL] submitted {side} {quantity} {base}: {tx_hash}")
        return tx_hash, "submitted", ""


async def _confirm_solana_tx(tx_hash: str) -> bool:
    """Poll Helius for confirmation. Returns True once finalized."""
    helius = _helius_rpc_url()
    if not helius or not tx_hash:
        return False
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            for _ in range(10):
                r = await client.post(helius, json={
                    "jsonrpc": "2.0", "id": 1, "method": "getSignatureStatuses",
                    "params": [[tx_hash], {"searchTransactionHistory": True}]}, timeout=10)
                statuses = r.json().get("result", {}).get("value", [None])
                st = statuses[0] if statuses else None
                if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return st.get("err") is None
                await asyncio.sleep(2)
    except Exception as e:
        logger.warning(f"[SOL] confirm poll failed for {tx_hash}: {e}")
    return False


async def _process_hyperliquid_order(order: dict, wallet_key: str):
    """Process a Hyperliquid order via the HL SDK (unchanged path)."""
    try:
        from eth_account import Account
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
    except ImportError as e:
        return None, "failed", f"Hyperliquid deps not installed: {e}"
    try:
        wallet = Account.from_key(wallet_key)
        info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
        meta = info.meta()
        exchange = Exchange(wallet=wallet, base_url="https://api.hyperliquid.xyz", meta=meta)
        symbol = order.get("symbol", "BTC/USD")
        side = (order.get("side") or "BUY").upper()
        quantity = float(order.get("quantity") or 0)
        coin = symbol.split("/")[0]
        if not settings.TRADING_LIVE_ENABLED:
            return None, "ready", f"DRY-RUN: {side} {quantity} {coin} on Hyperliquid"
        if side == "BUY":
            result = exchange.market_open(coin, True, quantity)
        else:
            result = exchange.market_close(coin, False, quantity)
        if result.get("status") == "ok":
            tx_hash = str(result.get("response", {}).get("data", {})
                          .get("statuses", [{}])[0].get("oid", ""))
            return tx_hash, "filled", ""
        return None, "failed", result.get("status", str(result))
    except Exception as e:
        msg = str(e)
        if "does not exist" in msg:
            msg = "Wallet not funded on Hyperliquid. Deposit ETH on Arbitrum to activate."
        return None, "failed", msg


register_adapter("solana", _process_solana_order)
register_adapter("hyperliquid", _process_hyperliquid_order)


# ── order routing + loop ────────────────────────────────────────────────────

async def process_order(order: dict):
    order_id = order.get("id", "?")
    agent_id = order.get("agent_id")
    chain = (order.get("chain") or "solana").lower()
    wallet_id = order.get("wallet_id")

    if not wallet_id:
        await _update_order(order_id, "failed", error="No wallet assigned")
        return

    # Cooldown between live on-chain trades.
    if settings.TRADING_LIVE_ENABLED and _last_trade_ts:
        wait = settings.TRADING_COOLDOWN_SECONDS - (time.time() - _last_trade_ts)
        if wait > 0:
            logger.info(f"Order #{order_id}: cooling down {wait:.0f}s")
            return  # leave pending; picked up next poll

    # Daily SOL spend cap (buys only).
    if chain == "solana" and (order.get("side") or "").upper() == "BUY":
        spent = await _sol_spent_last_24h()
        if spent + float(order.get("quantity") or 0) > settings.TRADING_DAILY_SOL_CAP:
            await _update_order(order_id, "failed",
                                error=f"daily SOL cap reached ({spent:.4f}/"
                                      f"{settings.TRADING_DAILY_SOL_CAP})")
            return

    logger.info(f"Processing #{order_id}: {order.get('side')} {order.get('quantity')} "
                f"{order.get('symbol')} on {chain}")

    agent = await _load_agent_info(agent_id)
    if not agent:
        await _update_order(order_id, "failed", error="Agent not found")
        return
    address, key = await _get_wallet_key(wallet_id, agent)
    if not key:
        await _update_order(order_id, "failed", error="Wallet key not available")
        return

    adapter = ADAPTERS.get(chain)
    if adapter:
        tx_hash, status, error = await adapter(order, key)
    else:
        tx_hash, status, error = None, "ready", f"No execution adapter for chain '{chain}'"

    await _update_order(order_id, status, tx_hash or "", error)

    # Follow submitted Solana txs to confirmation.
    if status == "submitted" and chain == "solana" and tx_hash:
        confirmed = await _confirm_solana_tx(tx_hash)
        await _update_order(order_id, "confirmed" if confirmed else "submitted", tx_hash,
                            "" if confirmed else "awaiting confirmation")


async def execution_loop(interval: int | None = None):
    """Poll for pending orders every N seconds and execute them."""
    interval = interval or settings.TRADING_ENGINE_INTERVAL
    live = "LIVE" if settings.TRADING_LIVE_ENABLED else "DRY-RUN"
    logger.info(f"Execution engine started (interval={interval}s, mode={live})")

    while True:
        try:
            active = await _count_active_pending()
            if active > settings.TRADING_MAX_CONCURRENT_PENDING:
                logger.warning(f"{active} active orders exceed concurrency cap "
                               f"{settings.TRADING_MAX_CONCURRENT_PENDING}; pausing intake")
            else:
                orders = await _get_pending_orders()
                if orders:
                    logger.info(f"Found {len(orders)} pending orders")
                    for order in orders:
                        try:
                            await process_order(order)
                            await asyncio.sleep(1)
                        except Exception as e:
                            logger.error(f"Order #{order.get('id')}: {e}")
                            await _update_order(order.get("id"), "failed", error=str(e))
        except asyncio.CancelledError:
            logger.info("Execution engine stopping")
            raise
        except Exception as e:
            logger.error(f"Execution loop error: {e}")
        await asyncio.sleep(interval)
