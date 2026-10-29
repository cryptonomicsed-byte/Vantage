"""External signal connector for trading mode.

Trading mode is surface-only by design: the lounge never creates orders, and
the palette hard-blocks /api/trading/orders. This connector receives an
external signal (a TradingView webhook or any JSON POST to TRADINGVIEW_
WEBHOOK_URL) and posts it to the lounge as a status envelope — it is an
inbox, never an execution path.

Without TRADINGVIEW_WEBHOOK_URL the connector reports configured=False and
the native signal bus (buzz_trading_channel) remains the only source.
"""
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_WEBHOOK = os.environ.get("TRADINGVIEW_WEBHOOK_URL", "").strip()


def is_configured() -> bool:
    return bool(_WEBHOOK)


async def forward_signal(symbol: str, side: str = "", price: str = "") -> dict:
    """Forward a signal to the configured webhook. Never trades."""
    if not is_configured():
        return {"configured": False, "note": "TRADINGVIEW_WEBHOOK_URL not set — native signal bus remains the default"}
    payload = {"symbol": symbol, "side": side, "price": price, "source": "vantage-lounge"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_WEBHOOK, json=payload)
        if resp.status_code not in (200, 201, 202, 204):
            return {"configured": True, "ok": False,
                    "error": f"webhook HTTP {resp.status_code}: {resp.text[:200]}"}
        return {"configured": True, "ok": True, "symbol": symbol, "side": side}
    except Exception as exc:
        logger.warning("tradingview connector failed: %s", exc)
        return {"configured": True, "ok": False, "error": f"unreachable: {exc}"}
