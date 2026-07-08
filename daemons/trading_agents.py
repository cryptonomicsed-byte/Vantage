#!/usr/bin/env python3
"""
TradingAgents Debate Trader — Multi-agent LLM trading signal generator.

Architecture:
  Analyst Agent    → Analyzes market data, identifies opportunities
  Technician Agent → Runs indicators, confirms technical setup
  Risk Manager     → Evaluates risk, adjusts conviction, sets stops
  Debate Synthesizer → Combines agent outputs into a final signal

Posts to Vantage /api/trading/signals/ingest with full audit trail.

Usage:
  python3 trading_agents.py              # single scan
  python3 trading_agents.py --daemon 300  # every 5 minutes
"""

import json, os, sys, time, logging, argparse
import urllib.request
from datetime import datetime, timezone

VANTAGE_URL = "http://127.0.0.1:8001"
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TAGENTS] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("trading_agents")


def call_vantage(endpoint: str, payload: dict = None) -> dict:
    """Call Vantage API endpoint."""
    url = f"{VANTAGE_URL}{endpoint}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
        method="POST" if payload else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def get_market_context() -> dict:
    """Gather market data for the agents to analyze."""
    context = {}

    # Top market data
    try:
        resp = call_vantage("/api/intel/market/top?limit=10")
        context["top_tokens"] = resp.get("tokens", [])[:10]
    except:
        pass

    # Signals
    try:
        resp = call_vantage("/api/intel/signals?limit=10")
        context["signals"] = resp.get("signals", [])[:10]
    except:
        pass

    # Sentiment
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/sentiment",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            context["sentiment"] = json.loads(resp.read())
    except:
        pass

    # Fear & Greed
    try:
        req = urllib.request.Request(
            "https://api.alternative.me/fng/?limit=1",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            fg = json.loads(resp.read())
            if fg.get("data"):
                context["fear_greed"] = fg["data"][0]
    except:
        pass

    return context


# ── Agent Analysis Functions ────────────────────────────────────────────

def analyst_agent(context: dict) -> dict:
    """Fundamental/macro analyst. Scores market conditions."""
    score = 50  # neutral
    reasoning = []

    # Fear & Greed
    fg = context.get("fear_greed", {})
    fg_value = int(fg.get("value", 50))
    if fg_value <= 25:
        score += 15
        reasoning.append(f"Extreme fear ({fg_value}) — contrarian BUY signal")
    elif fg_value <= 40:
        score += 5
        reasoning.append(f"Fear ({fg_value}) — cautious opportunity")
    elif fg_value >= 75:
        score -= 15
        reasoning.append(f"Extreme greed ({fg_value}) — overbought, SELL pressure")
    elif fg_value >= 60:
        score -= 5
        reasoning.append(f"Greed ({fg_value}) — elevated risk")

    # Market breadth
    sentiment = context.get("sentiment", {})
    breadth = sentiment.get("sentiment", {}).get("gainers_pct", 50)
    if breadth < 30:
        score -= 10
        reasoning.append(f"Low breadth ({breadth}% gainers) — bearish")
    elif breadth > 70:
        score += 10
        reasoning.append(f"High breadth ({breadth}% gainers) — bullish")

    # BTC dominance
    btc_dom = sentiment.get("sentiment", {}).get("btc_dominance", 55)
    if btc_dom > 60:
        reasoning.append(f"BTC dominance high ({btc_dom:.1f}%) — altcoin weakness")

    direction = "BUY" if score >= 55 else "SELL" if score <= 45 else "HOLD"
    conviction = abs(score - 50) / 50  # 0-1 scale

    return {
        "agent": "analyst",
        "direction": direction,
        "conviction": round(conviction, 2),
        "score": score,
        "reasoning": reasoning,
    }


def technician_agent(context: dict) -> dict:
    """Technical analyst. Evaluates signals and price action."""
    signals = context.get("signals", [])
    direction = "HOLD"
    conviction = 0
    reasoning = []

    buy_signals = [s for s in signals if s.get("conviction", 0) >= 3 and s.get("type") in ("trending", "alpha")]
    sell_signals = [s for s in signals if s.get("conviction", 0) >= 3 and s.get("type") == "arbitrage"]

    if len(buy_signals) >= 3:
        direction = "BUY"
        conviction = min(0.8, len(buy_signals) * 0.2)
        reasoning.append(f"{len(buy_signals)} trending/alpha BUY signals")
    elif len(sell_signals) >= 2:
        direction = "SELL"
        conviction = min(0.7, len(sell_signals) * 0.25)
        reasoning.append(f"{len(sell_signals)} arbitrage SELL signals")

    # Top movers momentum
    tokens = context.get("top_tokens", [])
    up = [t for t in tokens if (t.get("price_change_pct_24h") or 0) > 2]
    down = [t for t in tokens if (t.get("price_change_pct_24h") or 0) < -2]
    if len(up) > len(down) * 2:
        reasoning.append(f"Momentum: {len(up)} gainers vs {len(down)} losers — bullish")
        conviction += 0.1
    elif len(down) > len(up) * 2:
        reasoning.append(f"Momentum: {len(down)} losers vs {len(up)} gainers — bearish")

    return {
        "agent": "technician",
        "direction": direction,
        "conviction": round(min(conviction, 1.0), 2),
        "reasoning": reasoning,
    }


def risk_manager_agent(context: dict, analyst: dict, technician: dict) -> dict:
    """Risk manager. Adjusts conviction based on risk factors."""
    adjustments = []
    final_conviction = (analyst["conviction"] + technician["conviction"]) / 2

    # If agents disagree, reduce conviction
    if analyst["direction"] != technician["direction"]:
        final_conviction *= 0.5
        adjustments.append("Agent disagreement — conviction halved")

    # Market cap context
    tokens = context.get("top_tokens", [])
    if tokens:
        mcap = tokens[0].get("market_cap", 0)
        if mcap < 2e12:
            adjustments.append(f"Market cap ${mcap/1e12:.1f}T — below ATH levels")

    # Fear & Greed extreme = higher risk
    fg = context.get("fear_greed", {})
    fg_value = int(fg.get("value", 50))
    if fg_value <= 15 or fg_value >= 85:
        final_conviction *= 0.7
        adjustments.append("Extreme sentiment — reduced position size")

    # Determine consensus direction
    if analyst["direction"] == technician["direction"]:
        direction = analyst["direction"]
    else:
        # Analyst wins ties on fundamentals
        direction = analyst["direction"] if analyst["conviction"] >= technician["conviction"] else technician["direction"]

    return {
        "agent": "risk_manager",
        "direction": direction,
        "conviction": round(min(final_conviction, 1.0), 2),
        "adjustments": adjustments,
    }


# ── Main ────────────────────────────────────────────────────────────────

def run_debate():
    """Run full multi-agent debate cycle."""
    log.info("Starting agent debate cycle...")

    context = get_market_context()
    if not context.get("top_tokens"):
        log.warning("No market data available — skipping")
        return None

    # Agent 1: Analyst
    analyst = analyst_agent(context)
    log.info(f"Analyst: {analyst['direction']} ({analyst['conviction']:.2f}) — "
             f"{'; '.join(analyst['reasoning'][:2])}")

    # Agent 2: Technician
    technician = technician_agent(context)
    log.info(f"Technician: {technician['direction']} ({technician['conviction']:.2f}) — "
             f"{'; '.join(technician['reasoning'][:2])}")

    # Agent 3: Risk Manager
    risk = risk_manager_agent(context, analyst, technician)
    log.info(f"Risk Manager: {risk['direction']} ({risk['conviction']:.2f}) — "
             f"{'; '.join(risk['adjustments'][:2])}")

    # Build final signal
    signal = {
        "symbol": "BTC/USD",  # Primary signal — can expand to top movers
        "direction": risk["direction"],
        "conviction": risk["conviction"],
        "chain": "bitcoin",
        "source": "trading-agents",
        "details": json.dumps({
            "analyst": analyst,
            "technician": technician,
            "risk_manager": risk,
            "market_context": {
                "fear_greed": context.get("fear_greed", {}).get("value"),
                "top_mover": context["top_tokens"][0]["symbol"] if context.get("top_tokens") else None,
                "signal_count": len(context.get("signals", [])),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    }

    # Post to Vantage
    result = call_vantage("/api/trading/signals/ingest", {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "conviction": signal["conviction"],
        "chain": signal["chain"],
        "source": signal["source"],
        "details": signal["details"],
    })

    if "error" not in result:
        log.info(f"✅ Signal posted: {risk['direction']} conviction={risk['conviction']:.2f}")

        # Also post to feed for visibility
        try:
            direction_emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
            feed_payload = json.dumps({
                "title": f"🤖 Agent Debate: {signal['direction']} ({signal['conviction']:.1%} conviction)",
                "content": f"**3-agent debate** result: **{signal['direction']}** (conviction: {signal['conviction']:.1%}). "
                           f"Analyst: {analyst['direction']} ({analyst['conviction']:.0%}) | "
                           f"Technician: {technician['direction']} ({technician['conviction']:.0%}) | "
                           f"Risk: {', '.join(risk['adjustments'][:1])}",
                "tags": ["signal", "agent_debate", signal["direction"].lower()],
            }).encode()
            req2 = urllib.request.Request(
                f"{VANTAGE_URL}/api/trading/signals/ingest",
                data=feed_payload,
                headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
            )
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                log.info(f"📡 Feed post: {json.loads(resp2.read()).get('broadcast_id', 'ok')}")
        except Exception as e:
            log.debug(f"Feed post skipped: {e}")

        # Also post to signals pool for Trading dashboard
        try:
            sig_payload = json.dumps({
                "symbol": signal["symbol"].split("/")[0],
                "source": "trading_agents",
                "type": "debate",
                "conviction": signal["conviction"],
                "direction": signal["direction"],
                "detail": f"analyst={analyst['direction']} tech={technician['direction']} risk={risk['direction']}",
            }).encode()
            req3 = urllib.request.Request(
                f"{VANTAGE_URL}/api/intel/signals/ingest",
                data=sig_payload,
                headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY},
            )
            with urllib.request.urlopen(req3, timeout=5) as resp3:
                pass
        except Exception:
            pass
    else:
        log.error(f"❌ Post failed: {result['error']}")

    return signal


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingAgents Debate Trader")
    parser.add_argument("--daemon", type=int, nargs="?", const=300, metavar="SECONDS",
                        help="Run continuously (default 300s)")
    args = parser.parse_args()

    if args.daemon:
        log.info(f"TradingAgents daemon — debating every {args.daemon}s")
        while True:
            try:
                run_debate()
            except Exception as e:
                log.error(f"Debate error: {e}")
            time.sleep(args.daemon)
    else:
        result = run_debate()
        if result:
            print(json.dumps(result, indent=2, default=str))
