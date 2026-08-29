"""InsightX Network API client — real, independent wallet-labeling and
token-safety signals, feeding into degen_filters.py's existing gates as an
ADDITIONAL signal alongside Vantage's own real data (wallet_reputation,
mint/freeze-authority checks) -- never silently replacing them.

Real endpoint facts, verified empirically against the live API 2026-08-29
(key confirmed valid server-side, INSIGHTX_API_KEY on hostinger-vps) plus
docs.insightx.network's reference pages:
  base:  https://api.insightx.network
  auth:  header `X-API-Key`, raw key, no Bearer prefix (docs.insightx.network/
    reference/auth.md; confirmed live -- a bare GET to /labels/v1/sol/<addr>
    and /scanner/v1/tokens/sol/<addr> both returned real 200s with real data,
    not 401s, using this exact header)
  rate limits (confirmed live via response headers on a real call):
    per-minute: X-RateLimit-Minute-Remaining dropped from an unknown start to
      2 after just 2-3 calls in quick succession -- genuinely tight, treat as
      ~3-5 req/min, not the generous "free tier" numbers other APIs in this
      repo have (contrast Nansen's 15 req/sec).
    per-month: X-Quota-Month-Remaining showed 997 remaining (~1000/month
      total budget) -- this is a SCARCE resource, not a "cache for latency"
      resource. Every design choice below is driven by this, not just
      politeness.

Endpoints wired here:
  GET /labels/v1/{network}/{addresses}  (addresses = comma-separated, max
    100 per call -- confirmed live: real Binance hot-wallet address returned
    [{"address":..., "label":"Binance: Hot Wallet", "tags":["exchange"],
    "smart_contract":false}], an unlabeled address returned []).
  GET /scanner/v1/tokens/{network}/{token_address}  (one token per call, no
    batch form exists -- confirmed live: unknown/nonexistent token returned
    {"results":{"simple":{"score":0,"message":"High risk","reasons":
    ["Token not found."]}}} rather than a 404, so a low score alone does NOT
    mean "flagged risky", it can also mean "InsightX has no data" -- callers
    must check for that reasons string, not just threshold the score).

Rate-limit discipline (the real reason this file looks more defensive than
nansen_client.py):
  - Labels: batched (up to 100 addresses/call) AND per-address cached with a
    long TTL, so overlapping candidate pools across repeated aggregate-score
    runs mostly hit cache, and only genuinely-new addresses cost a call.
  - Scanner: no batch form exists server-side, so a hard per-process,
    per-minute call budget (_SCANNER_CALLS_PER_MINUTE_CAP) is enforced
    client-side BEFORE any network call -- once the cap is hit for the
    current minute, further requests return None immediately (fail-soft,
    same as "no key configured") rather than risking a real 429 or burning
    the tight monthly quota on a burst. Combined with a long cache TTL, this
    means only a handful of genuinely-new tokens get scanned per minute,
    with the rest naturally deferred to a later run once cached.

Fail-soft throughout, same convention as nansen_client.py/market_sources.py:
missing key / any HTTP failure / malformed body / budget exhausted returns
None / {} / [], never raises -- Vantage must never block real scoring or
filtering on InsightX being down, unconfigured, or rate-limited.
"""
import logging
import os
import time
from typing import Optional, Union

import httpx

from .market_sources import _cache_get, _cache_put

logger = logging.getLogger(__name__)

INSIGHTX_API_BASE = "https://api.insightx.network"

# Labels rarely change for a given address (exchange wallets, known
# contracts) -- a long TTL is both correct and, given the scarce monthly
# quota, the responsible choice.
_LABELS_TTL = 21_600.0  # 6h
# Token security posture (mint/freeze authority, honeypot state) can change,
# but rarely inside a single trading session -- still long, for the same
# quota reason. Real disqualifying risk (drainable, active honeypot) is
# checked fresh enough at this TTL for a degen-plays screening use case.
_SCANNER_TTL = 21_600.0  # 6h

# Confirmed live: only ~2-3 calls remained after a handful of rapid requests.
# Conservative on purpose -- this is a hard client-side ceiling, checked
# BEFORE any network call, independent of what the server's own limit
# actually is.
_SCANNER_CALLS_PER_MINUTE_CAP = 3
_scanner_call_times: list[float] = []


def _insightx_key() -> str:
    # Deployed unprefixed on hostinger-vps (vantage.service.d/
    # insightx-api-key.conf), same pattern as NANSEN_API_KEY/HELIUS_API_KEY --
    # bypasses pydantic-settings' VANTAGE_ env_prefix, read directly from env.
    return os.environ.get("INSIGHTX_API_KEY", "")


async def _insightx_get(path: str, ttl: float) -> Optional[Union[dict, list]]:
    """GET the InsightX API, cached, fail-soft. Returns None on any failure
    (no key, HTTP error, rate-limited, malformed response) -- callers must
    treat None exactly like "no data", never raise or block real
    scoring/filtering on it."""
    key = _insightx_key()
    if not key:
        return None
    cache_key = f"insightx:{path}"
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{INSIGHTX_API_BASE}{path}",
                headers={"X-API-Key": key},
            )
            if resp.status_code == 429:
                logger.warning(
                    "insightx_client: rate limited on %s, retry-after=%s",
                    path, resp.headers.get("Retry-After"),
                )
                return None
            if resp.status_code != 200:
                logger.debug("insightx_client: %s failed: %d %s", path, resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            _cache_put(cache_key, data)
            return data
    except Exception as e:
        logger.debug("insightx_client: %s failed: %s", path, e)
        return None


async def labels_for_addresses(addresses: list[str], network: str = "sol") -> dict[str, dict]:
    """Real InsightX address labels for the given addresses, e.g.
    {"exchange"/"dex"/etc tags, human label, smart_contract flag}. Per-
    address cached (_LABELS_TTL) so repeated overlapping candidate pools
    only pay for genuinely-new addresses; batches up to 100 uncached
    addresses per real API call (the endpoint's own real max). Addresses
    with no InsightX label simply aren't keys in the result -- "no label"
    and "known to have no label" are the same real state here, unlike a
    numeric signal where that distinction would matter.

    Returns {} if no key configured, on any failure, or if `addresses` is
    empty -- never raises."""
    if not addresses:
        return {}
    unique_addrs = list(dict.fromkeys(addresses))  # de-dupe, preserve order

    out: dict[str, dict] = {}
    to_fetch: list[str] = []
    for addr in unique_addrs:
        cached = _cache_get(f"insightx:label:{network}:{addr}", _LABELS_TTL)
        if cached is not None:
            if cached:  # cached {} means "confirmed no label", skip re-adding
                out[addr] = cached
        else:
            to_fetch.append(addr)

    if not to_fetch:
        return out

    # Real endpoint max is 100 comma-separated addresses per call.
    for i in range(0, len(to_fetch), 100):
        batch = to_fetch[i:i + 100]
        data = await _insightx_get(f"/labels/v1/{network}/{','.join(batch)}", _LABELS_TTL)
        labeled_in_batch = set()
        if isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                addr = row.get("address")
                if not addr:
                    continue
                labeled_in_batch.add(addr)
                entry = {
                    "label": row.get("label"),
                    "tags": row.get("tags") or [],
                    "smart_contract": bool(row.get("smart_contract")),
                }
                out[addr] = entry
                _cache_put(f"insightx:label:{network}:{addr}", entry)
        # Addresses in this batch InsightX returned nothing for: cache a
        # definite "no label" ({}) so a future overlapping call doesn't
        # re-spend quota re-asking about an address confirmed unlabeled.
        for addr in batch:
            if addr not in labeled_in_batch:
                _cache_put(f"insightx:label:{network}:{addr}", {})

    return out


def _scanner_budget_available() -> bool:
    """True if a new (non-cached) Scanner call is allowed under the
    client-side per-minute cap right now. Prunes call timestamps older than
    60s first."""
    now = time.time()
    while _scanner_call_times and (now - _scanner_call_times[0]) >= 60.0:
        _scanner_call_times.pop(0)
    return len(_scanner_call_times) < _SCANNER_CALLS_PER_MINUTE_CAP


async def scanner_for_token(token_address: str, network: str = "sol") -> Optional[dict]:
    """Real InsightX Scanner security posture for one token: safety score,
    honeypot/drainable/renounced flags, reasons. Cached (_SCANNER_TTL); if
    not cached AND the client-side per-minute call budget is exhausted,
    returns None immediately without attempting a network call (fail-soft,
    same as "no data") -- callers must never disqualify a token on None,
    only on a definite risk flag, same discipline as aggregate_score.py's
    existing mint/freeze-authority check.

    Returns None if no key configured, budget exhausted, `token_address` is
    empty, or on any failure -- never raises. Note InsightX itself returns
    a real 200 with score=0/"Token not found" for unknown tokens rather than
    a 404 (confirmed live) -- this function passes that through as-is;
    callers should treat reasons containing "not found" as "no data", not
    as a confirmed risk flag."""
    if not token_address:
        return None
    # Must match _insightx_get's own cache key (f"insightx:{path}") exactly --
    # a real bug caught by this file's own test suite: this used to check a
    # DIFFERENT key ("insightx:scanner:...") than _insightx_get actually
    # writes to, so this cache check could never hit, silently defeating
    # both the TTL cache AND (worse) meaning a cached lookup still counted
    # against the scarce per-minute budget below on every call.
    path = f"/scanner/v1/tokens/{network}/{token_address}"
    cache_key = f"insightx:{path}"
    cached = _cache_get(cache_key, _SCANNER_TTL)
    if cached is not None:
        return cached

    if not _scanner_budget_available():
        logger.debug(
            "insightx_client: scanner call budget exhausted for this minute, skipping %s",
            token_address,
        )
        return None

    _scanner_call_times.append(time.time())
    data = await _insightx_get(path, _SCANNER_TTL)
    if not isinstance(data, dict):
        return None
    return data


def scanner_risk_flags(scan: Optional[dict]) -> dict:
    """Normalize a raw scanner_for_token() result into the specific real
    disqualifying flags degen_filters-style gates care about. Returns
    {"has_data": bool, "drainable": bool, "renounced": bool, "score":
    Optional[int]} -- has_data=False means InsightX had nothing real to say
    (e.g. "Token not found", or scan was None), which callers must treat as
    "no signal" not "safe" or "risky"."""
    if not scan:
        return {"has_data": False, "drainable": False, "renounced": False, "score": None}
    results = scan.get("results") or {}
    simple = results.get("simple") or {}
    advanced = results.get("advanced") or {}
    reasons = simple.get("reasons") or []
    not_found = any("not found" in str(r).lower() for r in reasons) if isinstance(reasons, list) else False
    if not_found or not advanced:
        return {"has_data": False, "drainable": False, "renounced": False, "score": simple.get("score")}
    return {
        "has_data": True,
        "drainable": bool(advanced.get("drainable")),
        "renounced": bool(advanced.get("renounced")),
        "score": simple.get("score"),
    }
