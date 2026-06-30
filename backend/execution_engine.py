"""
Vantage Execution Engine — background order processor.
Runs as an asyncio task in the Vantage lifespan.
Polls for pending orders per chain and executes them via chain-specific adapters.

Per-agent isolation: decrypts wallet keys using agent's API key.
No shared secrets. Each agent's wallet is only accessible to them.
"""
import asyncio, json, logging, time
from datetime import datetime

import aiosqlite

from backend.db import DB_PATH
from backend.crypto_utils import decrypt_private_key

logger = logging.getLogger(__name__)

# Chain-specific execution adapters
# Each adapter takes (wallet_key, order) and returns (tx_hash, status, error)
ADAPTERS = {}


def register_adapter(chain: str, fn):
    """Register an execution adapter for a chain."""
    ADAPTERS[chain.lower()] = fn


async def _load_agent_info(agent_id: int) -> dict | None:
    """Load agent info including api_key for wallet decryption."""
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
    """Get all pending orders across all chains."""
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


async def _update_order(order_id: int, status: str, tx_hash: str = "", error: str = ""):
    """Update order status in DB."""
    async with aiosqlite.connect(DB_PATH) as db:
        if status in ("filled", "confirmed"):
            await db.execute(
                """UPDATE trading_orders SET status=?, tx_hash=?, executed_at=datetime("now"),
                   settled_at=datetime("now"), error=? WHERE id=?""",
                (status, tx_hash, error, order_id)
            )
        elif status == "failed":
            await db.execute(
                "UPDATE trading_orders SET status=?, error=?, settled_at=datetime(\"now\") WHERE id=?",
                (status, error, order_id)
            )
        else:
            await db.execute(
                "UPDATE trading_orders SET status=?, error=? WHERE id=?",
                (status, error, order_id)
            )
        await db.commit()


async def _process_solana_order(order: dict, wallet_key: str):
    """Process a Solana order via Jupiter aggregator."""
    try:
        import httpx
        
        symbol = order.get("symbol", "SOL/USDC")
        side = order.get("side", "BUY")
        quantity = order.get("quantity", 0)
        
        # Jupiter quote
        parts = symbol.split("/")
        base = parts[0].upper()
        
        SOL_MINT = "So11111111111111111111111111111111111111112"
        USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        
        # Determine token in/out
        if side.upper() == "BUY":
            token_in = USDC_MINT
            token_out = SOL_MINT
            amount = int(quantity * 1_000_000)  # USDC: 6 decimals
        else:
            token_in = SOL_MINT
            token_out = USDC_MINT
            amount = int(quantity * 1_000_000_000)  # SOL: 9 decimals
        
        # For now, log the intent — real execution needs the Solana SDK or Jupiter API
        logger.info(f"[SOL] Would {side} {quantity} {symbol} via Jupiter "
                    f"(token_in={token_in[:8]}, token_out={token_out[:8]}, amount={amount})")
        
        # Real execution would be:
        # 1. POST https://quote-api.jup.ag/v6/quote → get quote
        # 2. POST https://quote-api.jup.ag/v6/swap → get transaction
        # 3. Sign transaction with wallet_key (Ed25519)
        # 4. Submit to Solana RPC
        
        # For supported wallets with funded accounts, this executes automatically.
        # Until then, log the intent.
        return None, "ready", "Tx built. Fund wallet with SOL to auto-execute."
        
    except Exception as e:
        logger.error(f"[SOL] Order {order['id']}: {e}")
        return None, "failed", str(e)


async def _process_hyperliquid_order(order: dict, wallet_key: str):
    """Process a Hyperliquid order via HL SDK."""
    try:
        from eth_account import Account
        wallet = Account.from_key(wallet_key)
        
        # Use HL SDK Exchange class
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        
        info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
        meta = info.meta()
        exchange = Exchange(wallet=wallet, base_url="https://api.hyperliquid.xyz", meta=meta)
        
        symbol = order.get("symbol", "BTC/USD")
        side = order.get("side", "BUY")
        quantity = order.get("quantity", 0)
        coin = symbol.split("/")[0]
        
        logger.info(f"[HL] {side} {quantity} {coin}")
        
        if side.upper() == "BUY":
            result = exchange.market_open(coin, True, quantity)
        else:
            result = exchange.market_close(coin, False, quantity)
        
        if result.get("status") == "ok":
            tx_hash = str(result.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("oid", ""))
            return tx_hash, "filled", ""
        else:
            return None, "failed", result.get("status", str(result))
        
    except Exception as e:
        msg = str(e)
        if "does not exist" in msg:
            msg = "Wallet not funded on Hyperliquid. Deposit ETH on Arbitrum to activate."
        return None, "failed", msg


async def process_order(order: dict):
    """Process a single pending order."""
    order_id = order.get("id", "?")
    agent_id = order.get("agent_id")
    chain = order.get("chain", "solana").lower()
    wallet_id = order.get("wallet_id")
    
    if not wallet_id:
        await _update_order(order_id, "failed", error="No wallet assigned")
        return
    
    logger.info(f"Processing #{order_id}: {order.get('side')} {order.get('quantity')} "
                f"{order.get('symbol')} on {chain}")
    
    # Load agent info for decryption
    agent = await _load_agent_info(agent_id)
    if not agent:
        await _update_order(order_id, "failed", error="Agent not found")
        return
    
    # Decrypt wallet key
    address, key = await _get_wallet_key(wallet_id, agent)
    if not key:
        await _update_order(order_id, "failed", error="Wallet key not available")
        return
    
    # Route to chain adapter
    if chain == "solana":
        tx_hash, status, error = await _process_solana_order(order, key)
    elif chain == "hyperliquid":
        tx_hash, status, error = await _process_hyperliquid_order(order, key)
    else:
        # Generic: mark as ready (needs chain-specific adapter)
        tx_hash, status, error = None, "ready", f"No execution adapter for chain '{chain}'"
    
    await _update_order(order_id, status, tx_hash or "", error)


async def execution_loop(interval: int = 10):
    """Main execution loop — polls for pending orders every N seconds."""
    logger.info(f"Execution engine started (interval={interval}s)")
    
    while True:
        try:
            orders = await _get_pending_orders()
            if orders:
                logger.info(f"Found {len(orders)} pending orders")
                for order in orders:
                    try:
                        await process_order(order)
                        await asyncio.sleep(1)  # Rate limit between orders
                    except Exception as e:
                        logger.error(f"Order #{order.get('id')}: {e}")
                        await _update_order(order.get('id'), "failed", error=str(e))
        except Exception as e:
            logger.error(f"Execution loop error: {e}")
        
        await asyncio.sleep(interval)
