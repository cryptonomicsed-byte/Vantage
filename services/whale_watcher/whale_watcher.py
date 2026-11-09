"""
Whale Watcher — monitors on-chain wallet activity for large movements.
Streams mempool + confirmed transactions to detect accumulation/distribution.
Emits WhaleAlert signals into the Confluence Matrix.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import structlog
from web3 import AsyncWeb3

log = structlog.get_logger(__name__)

WHALE_USD_THRESHOLD = float(os.environ.get("WHALE_USD_THRESHOLD", "500000"))


@dataclass
class WhaleAlert:
    tx_hash: str
    wallet_address: str
    direction: str  # "buy" | "sell" | "transfer"
    amount_usd: float
    token_symbol: str
    chain: str
    block_number: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    is_exchange: bool = False
    cluster_label: str | None = None


class WhaleWatcherAgent:
    """
    Connects to Ethereum (and EVM-compatible) nodes via WebSocket.
    Monitors pending transactions and confirmed blocks for whale-scale moves.
    """

    def __init__(
        self,
        rpc_wss: str | None = None,
        alchemy_key: str | None = None,
    ) -> None:
        self.alchemy_key = alchemy_key or os.environ.get("ALCHEMY_API_KEY", "")
        ws_url = rpc_wss or os.environ.get(
            "ETH_WSS_URL", f"wss://eth-mainnet.g.alchemy.com/v2/{self.alchemy_key}"
        )
        self.w3 = AsyncWeb3(AsyncWeb3.AsyncWebsocketProvider(ws_url))
        self._known_exchanges: set[str] = self._load_known_exchanges()

    async def stream_pending(self, on_alert, token_addresses: list[str] | None = None):
        """Subscribe to pending mempool transactions, filter whale-scale ERC-20 transfers."""
        log.info("whale_watcher_pending_stream_starting")
        async for tx_hash in await self.w3.eth.subscribe("pendingTransactions"):
            try:
                tx = await self.w3.eth.get_transaction(tx_hash)
                alert = await self._classify_transaction(tx, pending=True)
                if alert and alert.amount_usd >= WHALE_USD_THRESHOLD:
                    log.info("whale_alert", tx=tx_hash[:10], amount=alert.amount_usd)
                    await on_alert(alert)
            except Exception:
                pass

    async def stream_blocks(self, on_alert):
        """Subscribe to confirmed blocks and scan for whale transactions."""
        log.info("whale_watcher_block_stream_starting")
        async for block_event in await self.w3.eth.subscribe("newHeads"):
            block_num = block_event["number"]
            try:
                block = await self.w3.eth.get_block(block_num, full_transactions=True)
                for tx in block.transactions:
                    alert = await self._classify_transaction(tx)
                    if alert and alert.amount_usd >= WHALE_USD_THRESHOLD:
                        await on_alert(alert)
            except Exception as exc:
                log.error("block_scan_error", block=block_num, error=str(exc))

    async def _classify_transaction(self, tx, pending: bool = False) -> WhaleAlert | None:
        """Classify a transaction. Returns WhaleAlert if whale-scale, else None."""
        if tx is None or tx.get("value", 0) == 0:
            return None

        eth_value = self.w3.from_wei(tx["value"], "ether")
        # Rough ETH price — production: fetch from oracle
        eth_price_usd = await self._get_eth_price()
        amount_usd = float(eth_value) * eth_price_usd

        if amount_usd < WHALE_USD_THRESHOLD:
            return None

        from_addr = tx.get("from", "")
        to_addr = tx.get("to", "") or ""

        direction = (
            "sell"
            if self._is_exchange(to_addr)
            else ("buy" if self._is_exchange(from_addr) else "transfer")
        )

        return WhaleAlert(
            tx_hash=tx["hash"].hex(),
            wallet_address=from_addr,
            direction=direction,
            amount_usd=amount_usd,
            token_symbol="ETH",
            chain="ethereum",
            block_number=tx.get("blockNumber") or 0,
            is_exchange=self._is_exchange(from_addr) or self._is_exchange(to_addr),
        )

    async def _get_eth_price(self) -> float:
        """Fetch ETH/USD price from CoinGecko (cached in production via Redis)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "ethereum", "vs_currencies": "usd"},
                )
                return float(resp.json()["ethereum"]["usd"])
        except Exception:
            return 3000.0  # fallback

    def _is_exchange(self, address: str) -> bool:
        return address.lower() in self._known_exchanges

    def _load_known_exchanges(self) -> set[str]:
        return {
            "0x28c6c06298d514db089934071355e5743bf21d60",  # Binance hot wallet
            "0x21a31ee1afc51d94c2efccaa2092ad1028285549",  # Binance cold wallet
            "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43",  # Coinbase
            "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be",  # Binance 2
        }
