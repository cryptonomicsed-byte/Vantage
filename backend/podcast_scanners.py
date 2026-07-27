"""Real scanner-fed topics for the persona podcast channels -- the whole
point is these are NOT random freeform LLM prompts like the flagship
Agent.TV's TOPICS list. Each persona reads real, already-running data:

  - AI Daily Wire: real top AI headlines from Hacker News (public Algolia
    API, no key needed).
  - Crypto Tier One: the SAME intel_latest.json Hermes-Ares's own bridge
    reads to post "🧠 Intel Scan Complete" to the feed (chain health, BTC/
    ETH/SOL consensus price, arbitrage/anomaly counts) -- real live market
    state, not a canned "talk about crypto" prompt.
    (~/ares_intelligence/intel_latest.json)
  - Degen Frequency: the SAME ares_radar/latest.json feed the signal
    bridge scans for trending-token orders -- real pump.fun-style trending
    tokens/anomalies with actual volume/mcap/buy-sell numbers.
    (~/ares_radar/latest.json)

Each function returns a topic STRING fed straight into
podcast_engine.generate_dialogue_script as the dialogue prompt, so the
LLM is grounded in real specifics instead of writing generic filler.
"""
import json
import logging
import os
import random
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

HOME = Path(os.path.expanduser("~"))
INTEL_PATH = HOME / "ares_intelligence" / "intel_latest.json"
RADAR_PATH = HOME / "ares_radar" / "latest.json"

FALLBACK_AI_TOPIC = "the biggest recent developments in AI models and tooling"
FALLBACK_CRYPTO_TIER_TOPIC = "today's overall crypto market health across major chains"
FALLBACK_DEGEN_TOPIC = "the wildest trending pump.fun-style tokens right now"


async def ai_news_topic() -> str:
    """Real top AI-related stories from Hacker News (Algolia search API,
    public/no-key) -- grounds the AI Daily Wire episode in actual headlines
    instead of a static topic list."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"tags": "story", "query": "AI OR LLM OR OpenAI OR Anthropic OR Gemini", "hitsPerPage": 8},
            )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        titles = [h["title"] for h in hits if h.get("title")][:5]
        if not titles:
            return FALLBACK_AI_TOPIC
        headline_list = "; ".join(titles)
        return f"today's real AI news headlines -- react to and discuss these actual stories: {headline_list}"
    except Exception as e:
        logger.warning("ai_news_topic scan failed, using fallback: %s", e)
        return FALLBACK_AI_TOPIC


def _read_json(path: Path) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        logger.warning("failed reading %s: %s", path, e)
    return None


def crypto_tier_topic() -> str:
    """Real chain-health + price-consensus snapshot -- the same file
    Hermes-Ares's own bridge reads for its Intel Scan Complete feed posts."""
    d = _read_json(INTEL_PATH)
    if not d:
        return FALLBACK_CRYPTO_TIER_TOPIC
    chains = d.get("health", {}).get("chains", {})
    healthy = sum(1 for c in chains.values() if c.get("health") in ("ok", "healthy"))
    fusion = d.get("anomalies", {}).get("fusion", {})
    arb_count = len(d.get("arbitrage", {}).get("opportunities", []))
    btc = fusion.get("btc_consensus") or fusion.get("btc")
    eth = fusion.get("eth")
    sol = fusion.get("sol")
    parts = [f"{healthy}/{len(chains)} monitored chains healthy"]
    if btc:
        parts.append(f"BTC consensus price ~${btc:,.0f}")
    if eth:
        parts.append(f"ETH ~${eth:,.0f}")
    if sol:
        parts.append(f"SOL ~${sol:,.2f}")
    parts.append(f"{arb_count} live arbitrage opportunities detected")
    return "today's real institutional-grade market briefing -- " + ", ".join(parts)


def crypto_degen_topic() -> str:
    """Real trending-token snapshot from the same radar feed the signal
    bridge scans for orders -- actual tickers, volume, and price moves."""
    d = _read_json(RADAR_PATH)
    if not d:
        return FALLBACK_DEGEN_TOPIC
    trending = d.get("trending", [])[:4]
    if not trending:
        return FALLBACK_DEGEN_TOPIC
    lines = []
    for t in trending:
        lines.append(
            f"${t.get('symbol', '?')} (mcap ${t.get('mcap', 0):,.0f}, "
            f"1h volume ${t.get('volume_1h', 0):,.0f}, 6h change {t.get('change_6h', 0):+.0f}%)"
        )
    random.shuffle(lines)  # don't always open on the same token
    return "hype up and dig into these real trending tokens seen on-chain right now: " + "; ".join(lines)
