"""
Signal fusion: enriches a signal dict with context from Loom and strategy-lab
before it reaches the trading decision layer.

Loom (:8889)         GET /api/decide  -> {consensus: {direction, conviction, debate}}
strategy-lab (:8012) GET /api/convergence?token=ADDR -> {score, verdict, ...}

Both functions are fail-open: on any error they return the signal unchanged.
"""
import logging

logger = logging.getLogger(__name__)

LOOM_BASE = "http://localhost:8889"
STRATEGY_LAB_BASE = "http://localhost:8012"


async def enrich_from_loom(signal: dict) -> dict:
    """
    Calls Loom /api/decide with the signal context.
    Adds loom_conviction, loom_direction to signal dict.
    Fail-open: returns signal unchanged on any error.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LOOM_BASE}/api/decide")
        if r.status_code != 200:
            return signal
        data = r.json()
        consensus = data.get("consensus", {})
        signal = dict(signal)  # shallow copy to avoid mutation
        raw_direction = consensus.get("direction", "WAIT")
        # normalise LOOM direction to system_one vocabulary
        direction_map = {
            "UP": "up",
            "BUY": "up",
            "LONG": "up",
            "DOWN": "down",
            "SELL": "down",
            "SHORT": "down",
            "WAIT": "pass",
            "HOLD": "pass",
            "PASS": "pass",
        }
        signal["loom_direction"] = direction_map.get(
            str(raw_direction).upper(), "pass"
        )
        raw_conviction = consensus.get("conviction", 0)
        # Loom conviction is an int 0-10; normalise to 0.0-1.0
        try:
            signal["loom_conviction"] = float(raw_conviction) / 10.0
        except (TypeError, ValueError):
            signal["loom_conviction"] = 0.0
    except Exception as e:
        logger.debug("enrich_from_loom failed (fail-open): %s", e)
    return signal


async def enrich_from_context(signal: dict) -> dict:
    """
    Attaches non-venue evidence that matches the market question: a macro print
    (FRED), a forecast (Open-Meteo), a community probability (Manifold).

    Added to the signal, not merged into its prices: a crowd probability from a
    play-money venue is context for the decision, never a settlement source.

    Adds:
      context            list of {source, direction, confidence, summary, match_score}
      context_direction  majority direction across matches ("catalyst" wins ties)
      context_confidence mean confidence, weighted by how much actually matched
    Fail-open: returns the signal unchanged on any error.
    """
    try:
        from .signal_sources import store as context_store
    except ImportError:
        try:
            from signal_sources import store as context_store
        except Exception:
            return signal

    question = signal.get("question") or signal.get("title") or ""
    if not question:
        return signal

    try:
        matches = await context_store.matching(question, limit=3)
    except Exception as e:
        logger.debug("enrich_from_context failed (fail-open): %s", e)
        return signal
    if not matches:
        return signal

    out = dict(signal)
    ctx = []
    for m in matches:
        payload = m.get("payload") or {}
        summary = (
            payload.get("label")
            or payload.get("question")
            or payload.get("city")
            or m.get("market_id")
        )
        ctx.append({
            "source": m["source"],
            "signal_type": m["signal_type"],
            "market_id": m["market_id"],
            "direction": m["direction"],
            "confidence": m["confidence"],
            "summary": summary,
            "match_score": m.get("match_score", 0),
        })
    out["context"] = ctx

    votes: dict[str, float] = {}
    for c in ctx:
        votes[c["direction"]] = votes.get(c["direction"], 0.0) + c["confidence"]
    if votes:
        # Catalyst outranks a lean when it is present: "a print is due" is a
        # reason to be careful, not a direction.
        out["context_direction"] = (
            "catalyst" if "catalyst" in votes
            else max(votes.items(), key=lambda kv: kv[1])[0]
        )
        out["context_confidence"] = round(
            sum(c["confidence"] for c in ctx) / len(ctx), 4
        )
    return out


async def enrich_from_strategy_lab(signal: dict) -> dict:
    """
    Calls strategy-lab /api/convergence with the token address extracted from
    the signal (looks for 'token', 'mint', 'address' keys).
    Adds strategy_lab_score to signal dict.
    Fail-open: returns signal unchanged on any error.
    """
    token = (
        signal.get("token")
        or signal.get("mint")
        or signal.get("address")
        or ""
    )
    if not token:
        return signal
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{STRATEGY_LAB_BASE}/api/convergence",
                params={"token": token, "record": "0"},
            )
        if r.status_code != 200:
            return signal
        data = r.json()
        signal = dict(signal)
        raw_score = data.get("score")
        if raw_score is not None:
            try:
                signal["strategy_lab_score"] = float(raw_score)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        logger.debug("enrich_from_strategy_lab failed (fail-open): %s", e)
    return signal
