"""InsightX DEX Metrics API client -- real cluster/bundler wallet-behavior
detection, feeding into aggregate_score.py's manipulation_flags
disqualification as a real, independently-sourced ADDITIONAL check. Shares
insightx_client.py's auth/base-URL/fail-soft conventions but is a distinct
API family (`/dex-metrics/v1/...`, not `/labels/v1/...` or
`/scanner/v1/...`) with its own real facts below -- kept in its own module
rather than folded into insightx_client.py, matching how nansen_client.py
and insightx_client.py are already siblings rather than one merged file.

Real endpoint facts, verified empirically against the live API 2026-08-29
(same key, same server-side deploy as insightx_client.py) plus
docs.insightx.network's reference pages:
  GET /dex-metrics/v1/{network}/{token_address}/clusters
    Confirmed live shape: {"total_cluster_pct": <float>, "clusters": [...]}.
    Every real Solana pump.fun token probed this session (6 different live
    mints, current market caps $15k-$100k+) came back with an EMPTY
    clusters list -- a real, not a bug: this feature only fires when
    InsightX's own detection actually finds a coordinated wallet cluster,
    which most of tonight's sampled tokens simply didn't have. The
    per-cluster member shape (docs prose: "member wallets, balances,
    percentages, and tags") could not be empirically confirmed against a
    populated example this session -- parsed defensively below (.get()
    everywhere, unknown/extra fields simply pass through unused) rather
    than asserting an unverified exact schema.
  GET /dex-metrics/v1/{network}/{token_address}/bundlers
    Confirmed live shape, exact match to docs:
    {"total_bundlers_pct": <float>, "bundlers": [{"address", "balance",
    "percentage", "reasons": [...], "slot"}]}. Verified with real,
    non-empty data: a live pump.fun token returned dozens of real bundler
    wallets with reasons like "same_slot_as_pool" / "transfer_from_bundler".

Atlas API: deliberately NOT built. docs.insightx.network/reference/
atlas-api-overview.md states outright: "Atlas API endpoints has been
decommissioned. With the migration to Atlas Live, we no longer support the
old Atlas Platform... New endpoints for Atlas Live will be coming soon."
There is no real, live endpoint to integrate against as of this writing --
building against the old decommissioned paths would mean fabricating dead
code against a service that no longer exists. Revisit once InsightX
actually ships real Atlas Live endpoints.

Rate limits: same real, tight budget as insightx_client.py (confirmed via
the SAME response headers on this same account -- X-RateLimit-Minute-
Remaining, X-Quota-Month-Remaining -- this is one shared account-wide
quota across Labels/Scanner/DEX Metrics, not a separate pool per API
family). Reuses insightx_client.py's exact per-minute call-budget pattern
for both clusters and bundlers (no batch form exists for either), plus a
long cache TTL for the same reason.

Fail-soft throughout: missing key / any HTTP failure / malformed body /
budget exhausted returns None, never raises.
"""
import logging
import os
import time
from typing import Optional

import httpx

from .market_sources import _cache_get, _cache_put

logger = logging.getLogger(__name__)

DEX_METRICS_API_BASE = "https://api.insightx.network"

# Wallet-cluster/bundler composition for a given token is essentially
# static once trading has settled (bundlers form at launch, clusters form
# from historical accumulation) -- long TTL, same quota-conservation
# reasoning as insightx_client.py's Scanner cache.
_DEX_METRICS_TTL = 21_600.0  # 6h

# Shared account-wide quota with insightx_client.py's Scanner budget (same
# real per-minute limit, confirmed via the same response headers on the
# same account) -- a SEPARATE counter here, deliberately conservative
# (rather than trying to coordinate a single cross-module budget), so this
# client alone never bursts past what the account can sustain even if
# Scanner calls happen to be quiet at the same moment.
_DEX_METRICS_CALLS_PER_MINUTE_CAP = 3
_dex_metrics_call_times: list[float] = []


def _insightx_key() -> str:
    # Same INSIGHTX_API_KEY env var as insightx_client.py -- one real key,
    # one account, shared across every InsightX API family.
    return os.environ.get("INSIGHTX_API_KEY", "")


def _budget_available() -> bool:
    now = time.time()
    while _dex_metrics_call_times and (now - _dex_metrics_call_times[0]) >= 60.0:
        _dex_metrics_call_times.pop(0)
    return len(_dex_metrics_call_times) < _DEX_METRICS_CALLS_PER_MINUTE_CAP


async def _dex_metrics_get(path: str) -> Optional[dict]:
    """GET a DEX Metrics endpoint, cached, budget-checked, fail-soft.
    Returns None on any failure (no key, HTTP error, rate-limited,
    malformed response, or the per-minute budget already exhausted this
    minute) -- callers must treat None exactly like "no data"."""
    key = _insightx_key()
    if not key:
        return None
    cache_key = f"dexmetrics:{path}"
    cached = _cache_get(cache_key, _DEX_METRICS_TTL)
    if cached is not None:
        return cached

    if not _budget_available():
        logger.debug("dex_metrics_client: call budget exhausted for this minute, skipping %s", path)
        return None

    _dex_metrics_call_times.append(time.time())
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{DEX_METRICS_API_BASE}{path}",
                headers={"X-API-Key": key},
            )
            if resp.status_code == 429:
                logger.warning("dex_metrics_client: rate limited on %s", path)
                return None
            if resp.status_code != 200:
                logger.debug("dex_metrics_client: %s failed: %d %s", path, resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            _cache_put(cache_key, data)
            return data
    except Exception as e:
        logger.debug("dex_metrics_client: %s failed: %s", path, e)
        return None


async def clusters_for_token(token_address: str, network: str = "sol") -> Optional[dict]:
    """Real InsightX coordinated-wallet-cluster detection for one token.
    Returns {"total_cluster_pct": float, "clusters": [...]} on success (raw
    passthrough of clusters -- see this module's docstring on why the exact
    per-cluster member schema isn't asserted, only accessed defensively by
    callers), None on any failure/no data/budget exhausted."""
    if not token_address:
        return None
    return await _dex_metrics_get(f"/dex-metrics/v1/{network}/{token_address}/clusters")


async def bundlers_for_token(token_address: str, network: str = "sol") -> Optional[dict]:
    """Real InsightX bundler-wallet detection for one token (Solana only,
    per InsightX's own docs). Returns {"total_bundlers_pct": float,
    "bundlers": [{"address", "balance", "percentage", "reasons", "slot"}]}
    on success, None on any failure/no data/budget exhausted."""
    if not token_address:
        return None
    return await _dex_metrics_get(f"/dex-metrics/v1/{network}/{token_address}/bundlers")


def manipulation_signal(clusters: Optional[dict], bundlers: Optional[dict]) -> dict:
    """Normalize raw clusters_for_token()/bundlers_for_token() results into
    a single real manipulation signal, analogous in spirit to
    pumpfun_tier_scanner.py's own persisted manipulation_flags (see
    aggregate_score.py's step 3c) but independently sourced from InsightX
    rather than Vantage's own wash-trading detection.

    Returns {"has_data": bool, "cluster_pct": Optional[float],
    "bundler_pct": Optional[float], "flagged": bool}. "flagged" is True
    only when a real, non-trivial concentration is found in EITHER signal
    (>5% of supply held by a detected cluster or bundler ring) -- a small
    residual percentage (like the 0.0000004% bundler_pct seen live on a
    real, clean token this session) is real detection noise, not evidence
    of manipulation, so a real threshold is applied rather than "any
    nonzero value flags it"."""
    has_cluster_data = isinstance(clusters, dict)
    has_bundler_data = isinstance(bundlers, dict)
    cluster_pct = clusters.get("total_cluster_pct") if has_cluster_data else None
    bundler_pct = bundlers.get("total_bundlers_pct") if has_bundler_data else None

    FLAG_THRESHOLD_PCT = 5.0
    flagged = bool(
        (isinstance(cluster_pct, (int, float)) and cluster_pct > FLAG_THRESHOLD_PCT)
        or (isinstance(bundler_pct, (int, float)) and bundler_pct > FLAG_THRESHOLD_PCT)
    )
    return {
        "has_data": has_cluster_data or has_bundler_data,
        "cluster_pct": cluster_pct,
        "bundler_pct": bundler_pct,
        "flagged": flagged,
    }
