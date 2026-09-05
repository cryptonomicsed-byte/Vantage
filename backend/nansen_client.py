"""Nansen API client — real wallet-labeling / smart-money flow data,
feeding into aggregate_score.py's smart_money component as an ADDITIONAL
signal alongside the existing wallet_reputation/copy_trade_score source
(see that module for how the two are combined, clearly labeled, not
silently merged).

Real endpoint facts (docs.nansen.ai/llms-full.txt, verified 2026-08-29):
  base:  https://api.nansen.ai/api/v1
  auth:  header `apikey` (lowercase), raw key -- no Bearer prefix
  shape: nearly everything is POST with a JSON body, not GET+query params
  rate limits: free tier 15 req/sec / 300 req/min; 429 on excess, with
    Retry-After + X-RateLimit-* headers

Endpoints used here:
  POST /smart-money/holdings   body: {"chains": [...], "pagination": {...}}
    5 credits/req. Real smart-money wallet aggregate holdings per token:
    token_address, token_symbol, value_usd, holders_count,
    balance_24h_percent_change. This is Nansen's own curated "smart
    money" wallet cohort, independent of Vantage's own wallet_reputation
    scoring -- a second, differently-sourced conviction signal.

Endpoints researched but deliberately NOT wired in yet:
  POST /profiler/address/labels -- real wallet-labeling (exchange/CEX/
    fund/etc identification), which is exactly what degen_filters.py's
    is_major_or_stable() or a real bundler-ring detector could use. Not
    wired in this pass: it costs 100-500 credits PER ADDRESS (vs. 5
    credits for one holdings call covering the whole chain), and there's
    no per-mint or per-wallet-batch pricing break documented -- wiring it
    into a per-candidate loop (even the small ~20-40 candidate aggregate-
    score pool) would burn 2,000-20,000 credits per single aggregate-score
    request. That's a real, deliberate scope decision, not an oversight:
    worth building as a targeted, manually-triggered lookup (e.g. "label
    this one suspicious wallet") later, not as an always-on pipeline
    component at this credit cost.

Fail-soft throughout, same convention as every other client in this repo
(market_sources.py, moonshot_client.py): missing key / any HTTP failure /
malformed body returns {} or [] , never raises -- Vantage must never block
real scoring on Nansen being down or unconfigured.
"""
import logging
import os
import time
from typing import Optional

import httpx

from .market_sources import _cache_get, _cache_put

logger = logging.getLogger(__name__)

NANSEN_API_BASE = "https://api.nansen.ai/api/v1"
_HOLDINGS_TTL = 300.0  # 5 min -- holdings is 5 credits/req; bounds real cost
# across repeated aggregate-score requests (frontend polls ~every 90s).


def _nansen_key() -> str:
    # Deployed unprefixed on hostinger-vps (vantage.service.d/nansen-api-key.conf),
    # same pattern as HELIUS_API_KEY/JUPITER_API_KEY -- bypasses pydantic-settings'
    # VANTAGE_ env_prefix, read directly from the environment.
    return os.environ.get("NANSEN_API_KEY", "")


async def _nansen_post(path: str, body: dict, ttl: float) -> Optional[dict]:
    """POST to the Nansen API, cached, fail-soft. Returns None on any
    failure (no key configured, HTTP error, rate-limited, malformed
    response) -- callers must treat None exactly like "no data", never
    raise or block real scoring on it."""
    key = _nansen_key()
    if not key:
        return None
    cache_key = f"nansen:{path}:{body}"
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{NANSEN_API_BASE}{path}",
                json=body,
                headers={"apikey": key, "Content-Type": "application/json"},
            )
            if resp.status_code == 429:
                logger.warning("nansen_client: rate limited on %s, retry-after=%s", path, resp.headers.get("Retry-After"))
                return None
            if resp.status_code != 200:
                logger.debug("nansen_client: %s failed: %d %s", path, resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            _cache_put(cache_key, data)
            return data
    except Exception as e:
        logger.debug("nansen_client: %s failed: %s", path, e)
        return None


async def smart_money_holdings(chain: str = "solana") -> list[dict]:
    """Real Nansen smart-money aggregate holdings for a whole chain in one
    call (5 credits total, not per-token) -- normalized to
    {token_address, symbol, value_usd, holders_count, balance_change_24h_pct}.
    Empty list on any failure/no key/no data, never raises. Cached per-chain
    for _HOLDINGS_TTL so repeated aggregate-score requests within that
    window don't re-spend credits."""
    data = await _nansen_post("/smart-money/holdings", {"chains": [chain]}, _HOLDINGS_TTL)
    rows = data if isinstance(data, list) else (data or {}).get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "token_address": r.get("token_address"),
            "symbol": r.get("token_symbol"),
            "value_usd": r.get("value_usd"),
            "holders_count": r.get("holders_count"),
            "balance_change_24h_pct": r.get("balance_24h_percent_change"),
        })
    return out


async def smart_money_holdings_by_mint(mints: list[str], chain: str = "solana") -> dict[str, dict]:
    """Real Nansen smart-money holdings filtered down to the given mints,
    from the single cached whole-chain fetch above -- never one API call
    per mint. {mint: {value_usd, holders_count, balance_change_24h_pct}}
    for whichever of the given mints Nansen's smart-money cohort actually
    holds; mints with no Nansen data simply aren't keys in the result
    (not a 0.0 -- "no data" and "confirmed zero" are different states,
    callers decide how to treat a missing key)."""
    if not mints:
        return {}
    holdings = await smart_money_holdings(chain)
    wanted = set(mints)
    out = {}
    for h in holdings:
        addr = h.get("token_address")
        if addr in wanted:
            out[addr] = {
                "value_usd": h.get("value_usd"),
                "holders_count": h.get("holders_count"),
                "balance_change_24h_pct": h.get("balance_change_24h_pct"),
            }
    return out
