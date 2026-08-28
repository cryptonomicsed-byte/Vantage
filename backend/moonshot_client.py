"""Moonshot (moon.it) Data API client + bonding-curve math.

Moonshot is a Solana bonding-curve launchpad, distinct from pump.fun (its
own program/curve, its own Data API). Two real, verified-real pieces:

1. REST client against the documented public Data API
   (docs.moonshot.cc/developers/data-api). Spec fetched and verified
   2026-08-28 from the OpenAPI file the docs page links
   (openapi-spec.yml, servers: https://api.moonshot.cc). Real endpoints:
     GET /tokens/v1/{viewId}/{chainId}   viewId ∈ {trending,top,rising,new,finalized}
     GET /token/v1/{chainId}/{pairIdOrTokenId}
     GET /trades/v1/latest/{chainId}[/{pairIdOrTokenId}]
   No auth header documented. Rate limit: 600 req/min.

   IMPORTANT KNOWN ISSUE (as of this build, 2026-08-28): api.moonshot.cc
   does not resolve in DNS -- verified via direct nslookup against 8.8.8.8
   from hostinger-vps (NXDOMAIN, not a local/sandboxing artifact). This is
   either a not-yet-live endpoint or a docs/infra mismatch on Moonshot's
   side, not something wrong in this client. The client is written exactly
   to spec and fails soft (returns None), so Vantage degrades gracefully
   (Moonshot's platform-leader row shows "unavailable") rather than
   crashing or fabricating a result -- consistent with every other source
   in this file. If/when the endpoint comes online, this starts working
   with no code change needed. Flagged prominently here and in the
   platform-leaders endpoint's response so it's never silently mistaken
   for "no trending tokens right now."

2. Bonding-curve price/mcap math, ported from the official TypeScript SDK's
   documented formula (docs.moonshot.cc/developers/bonding-curve-solana):
   constant-product curve, price = vSOL / vToken (virtual reserves), mcap =
   price * 1_000_000_000 (Moonshot's fixed 1B total supply), and migration
   to a real DEX happens once roughly 80% of curve supply has been sold.
   This is a fallback path for computing price/mcap from raw on-chain
   virtual-reserve numbers if the Data API (which already returns priceUsd/
   marketCap directly) is unavailable -- kept small and pure (no RPC calls
   itself) so it's trivially testable and reusable.
"""
import logging
from typing import Optional

from .market_sources import _cache_get, _cache_put, _get_json

logger = logging.getLogger(__name__)

MOONSHOT_API_BASE = "https://api.moonshot.cc"
MOONSHOT_TOTAL_SUPPLY = 1_000_000_000  # fixed per Moonshot's bonding-curve spec
MOONSHOT_MIGRATION_PROGRESS_PCT = 80  # curve migrates once ~80% of supply is sold


def curve_price_and_mcap(v_sol: float, v_token: float) -> tuple[Optional[float], Optional[float]]:
    """Constant-product bonding-curve price + market cap from virtual
    reserves, per Moonshot's own documented formula:
        price (in SOL per token) = vSOL / vToken
        market cap (in SOL)      = price * MOONSHOT_TOTAL_SUPPLY
    Pure function, no I/O -- v_sol/v_token come from either the Data API's
    `moonshot.curvePosition`-derived reserves or a direct on-chain
    CurveAccount read (see the SDK's getCurveAccount.ts for the account
    layout; not implemented here since the Data API already returns
    price/mcap directly when reachable and this is only a documented
    fallback, not the primary path).
    Returns (None, None) if v_token is zero/missing (undefined division).
    """
    if not v_token:
        return None, None
    price = v_sol / v_token
    mcap = price * MOONSHOT_TOTAL_SUPPLY
    return price, mcap


async def _moonshot_get(path: str, ttl: float = 30.0):
    """GET against the Moonshot Data API, cached, fail-soft (None on any
    error -- unreachable host, timeout, non-200, malformed body). Never
    raises; callers treat a None exactly like an empty/unavailable source,
    same convention as every other client in market_sources.py."""
    key = f"moonshot:{path}"
    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached
    data = await _get_json(f"{MOONSHOT_API_BASE}{path}", timeout=8.0, retries=1)
    if data is not None:
        _cache_put(key, data)
    return data


async def moonshot_tokens(view_id: str = "top", chain_id: str = "solana") -> list[dict]:
    """Tokens for a given Moonshot ranking view (trending/top/rising/new/
    finalized), normalized to the fields Vantage's other platform sources
    already use elsewhere (symbol/address/price/market_cap/volume) so
    callers don't need Moonshot-specific field-name knowledge. Empty list
    on any failure (host unreachable, bad view_id, etc) -- never raises.
    """
    if view_id not in ("trending", "top", "rising", "new", "finalized"):
        view_id = "top"
    raw = await _moonshot_get(f"/tokens/v1/{view_id}/{chain_id}")
    if not isinstance(raw, list):
        return []
    out = []
    for t in raw:
        base = t.get("baseToken") or {}
        moonshot_meta = t.get("moonshot") or {}
        volume = ((t.get("volume") or {}).get("h24") or {}).get("total")
        price_change = (t.get("priceChange") or {}).get("h24")
        out.append({
            "symbol": base.get("symbol"),
            "name": base.get("name"),
            "address": base.get("address"),
            "pair_address": t.get("pairAddress"),
            "price_usd": t.get("priceUsd"),
            "market_cap": t.get("marketCap"),
            "fdv": t.get("fdv"),
            "volume_24h": volume,
            "price_change_24h": price_change,
            "liquidity_usd": (t.get("liquidity") or {}).get("usd"),
            "curve_progress_pct": moonshot_meta.get("progress"),
            "url": t.get("url"),
        })
    return out


async def moonshot_top_token(chain_id: str = "solana") -> Optional[dict]:
    """The #1 ranked token by Moonshot's own 'top' view -- the platform's
    native ranking metric, not something Vantage derives. Returns None if
    the API is unreachable or returns no tokens."""
    tokens = await moonshot_tokens("top", chain_id)
    return tokens[0] if tokens else None
