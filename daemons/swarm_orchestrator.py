#!/usr/bin/env python3
"""
Ghost Swarm Orchestrator — Multi-Agent Consensus Trading Signals

Port of swarm-orchestrator.ts + conflictResolver.ts logic.
Spawns 4 perspective agents per token (technical, sentiment, on-chain, social),
each powered by DeepSeek. When 2+ agents agree on BUY/SELL, posts a consensus
signal to Vantage. Uses meta-reasoning for conflict resolution.

Usage:
    python3 swarm_orchestrator.py --once          # single scan
    python3 swarm_orchestrator.py --daemon        # continuous loop
    python3 swarm_orchestrator.py --interval 300  # custom loop interval (sec)
"""

import argparse
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import requests  # for DeepSeek (OpenAI-compatible)

# ── Config ──────────────────────────────────────────────────
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")

# Database paths on the VPS
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
PUMPFUN_DB = "/opt/ares/Vantage/data/vantage.db"

AGENT_COUNT = 4            # technical, sentiment, on-chain, social
CONSENSUS_THRESHOLD = 2    # 2+ agents must agree
CONVICTION_FLOOR = 0.6     # minimum conviction for a BUY/SELL vote
POLL_INTERVAL = 120        # seconds between scans (default)

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SWARM] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/opt/ares/ares_logs/swarm_orchestrator.log", mode="a"),
    ],
)
log = logging.getLogger("swarm")

# ── Agent definitions (mirrors swarm-orchestrator.ts Agent interface) ──
AGENTS = [
    {
        "name": "Technical Analyst",
        "role": "analysis",
        "perspective": "technical",
        "system_prompt": (
            "You are a technical analysis expert for cryptocurrency trading. "
            "Analyze the given token using price action, volume, support/resistance, "
            "moving averages, RSI, MACD, and chart patterns. "
            "Respond with EXACTLY this format:\n"
            "SIGNAL: BUY|SELL|HOLD\n"
            "CONVICTION: 0.0-1.0\n"
            "REASONING: <2-3 sentences>\n"
            "No other text."
        ),
    },
    {
        "name": "Sentiment Analyst",
        "role": "analysis",
        "perspective": "sentiment",
        "system_prompt": (
            "You are a market sentiment analyst for cryptocurrency trading. "
            "Analyze the given token based on overall market sentiment, fear/greed "
            "indicators, social media trends, and community engagement. "
            "Respond with EXACTLY this format:\n"
            "SIGNAL: BUY|SELL|HOLD\n"
            "CONVICTION: 0.0-1.0\n"
            "REASONING: <2-3 sentences>\n"
            "No other text."
        ),
    },
    {
        "name": "On-Chain Analyst",
        "role": "analysis",
        "perspective": "onchain",
        "system_prompt": (
            "You are an on-chain data analyst for cryptocurrency trading. "
            "Analyze the given token using on-chain metrics: wallet activity, "
            "transfer volume, holder distribution, liquidity depth, smart money "
            "movements, and protocol health. "
            "Respond with EXACTLY this format:\n"
            "SIGNAL: BUY|SELL|HOLD\n"
            "CONVICTION: 0.0-1.0\n"
            "REASONING: <2-3 sentences>\n"
            "No other text."
        ),
    },
    {
        "name": "Social Intel Analyst",
        "role": "analysis",
        "perspective": "social",
        "system_prompt": (
            "You are a social intelligence analyst for cryptocurrency trading. "
            "Analyze the given token based on influencer activity, Telegram/Discord "
            "chatter, Twitter/X volume, meme traction, community growth rate, and "
            "narrative alignment. "
            "Respond with EXACTLY this format:\n"
            "SIGNAL: BUY|SELL|HOLD\n"
            "CONVICTION: 0.0-1.0\n"
            "REASONING: <2-3 sentences>\n"
            "No other text."
        ),
    },
]

# ── DeepSeek client ─────────────────────────────────────────
_client = None

def get_client():
    global _client
    if _client is None:
        _client = requests.Session()
        _client.headers.update({
            "Authorization": f"Bearer {DEEPSEEK_KEY}",
            "Content-Type": "application/json",
        })
    return _client


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.7) -> Tuple[str, int, float]:
    """
    Call DeepSeek API. Returns (content, tokens_used, cost_estimate).
    Mirrors callAgent() from swarm-orchestrator.ts.
    """
    client = get_client()
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": 300,
    }

    try:
        resp = client.post(
            f"{DEEPSEEK_BASE}/chat/completions",
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        # DeepSeek pricing approx: $0.27/1M input, $1.10/1M output
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = (prompt_tokens * 0.27 + completion_tokens * 1.10) / 1_000_000

        return content, tokens, cost
    except Exception as e:
        log.error(f"DeepSeek API call failed: {e}")
        raise


# ── Response parsing ────────────────────────────────────────
def parse_agent_response(text: str) -> Dict:
    """
    Extract SIGNAL, CONVICTION, and REASONING from agent response.
    """
    signal_match = re.search(r"SIGNAL:\s*(BUY|SELL|HOLD)", text, re.IGNORECASE)
    conviction_match = re.search(r"CONVICTION:\s*([\d.]+)", text)
    reasoning_match = re.search(r"REASONING:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)

    signal = signal_match.group(1).upper() if signal_match else "HOLD"
    conviction = float(conviction_match.group(1)) if conviction_match else 0.5
    reasoning = reasoning_match.group(1).strip() if reasoning_match else text[:200]

    # Clamp
    conviction = max(0.0, min(1.0, conviction))

    return {
        "signal": signal,
        "conviction": conviction,
        "reasoning": reasoning,
        "raw": text,
    }


# ── Conflict resolution (mirrors conflictResolver.ts) ────────
def detect_conflict(proposals: List[Dict]) -> bool:
    """
    Detect if proposals are in conflict: agents disagree on BUY vs SELL.
    Returns True if there's a meaningful disagreement.
    """
    if len(proposals) < 2:
        return False

    signals = [p["signal"] for p in proposals]
    has_buy = "BUY" in signals
    has_sell = "SELL" in signals
    has_hold = "HOLD" in signals

    # Conflict: both BUY and SELL present (direct opposition)
    if has_buy and has_sell:
        return True

    # Conflict: buy/hold or sell/hold with mixed conviction
    if len(set(signals)) > 1:
        return True

    return False


def resolve_by_voting(proposals: List[Dict]) -> Dict:
    """
    Voting resolution: weight by conviction. Highest-scored wins.
    Mirrors resolveByVoting() from conflictResolver.ts.
    """
    # Score = conviction * (1 if BUY, 0.5 if HOLD, 0 if SELL... but we weight differently)
    scores = []
    for p in proposals:
        # Clarity score based on response length
        clarity = min(80, len(p.get("raw", "")) / 5)
        # Feasibility based on conviction
        feasibility = p["conviction"] * 100
        cost = 80
        innovation = 65 if p["signal"] != "HOLD" else 50

        score = clarity * 0.25 + feasibility * 0.35 + cost * 0.2 + innovation * 0.2
        scores.append((p, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    winner = scores[0][0]
    return {
        "winner": winner,
        "strategy": "voting",
        "scores": [{"agent": p["agent"], "score": round(s, 1)} for p, s in scores],
    }


def resolve_by_merge(proposals: List[Dict]) -> Dict:
    """
    Hierarchical merge: combine all proposals.
    Mirrors resolveByMerge() from conflictResolver.ts.
    """
    merged = "\n\n---\n\n".join(
        f"From {p['agent']} ({p['perspective']}): {p['reasoning']}" for p in proposals
    )
    return {
        "winner": proposals[0],
        "strategy": "hierarchical",
        "merged": merged,
    }


def resolve_by_meta_reasoning(proposals: List[Dict], token_info: str) -> Dict:
    """
    Meta-reasoning: ask an LLM to synthesize conflicting proposals.
    Mirrors metaReasoningMerge() from swarm-orchestrator.ts.
    """
    proposal_text = "\n\n".join(
        f"Agent: {p['agent']} ({p['perspective']})\nSignal: {p['signal']}\nConviction: {p['conviction']:.2f}\nReasoning: {p['reasoning']}"
        for p in proposals
    )

    system_prompt = (
        "You are a meta-reasoning synthesis engine for crypto trading. "
        "You receive conflicting analyses from multiple AI agents and must "
        "synthesize them into a single BUY/SELL/HOLD decision. "
        "Weigh each agent's reasoning carefully. Output your synthesis "
        "in EXACTLY this format:\n"
        "SIGNAL: BUY|SELL|HOLD\n"
        "CONVICTION: 0.0-1.0\n"
        "REASONING: <synthesis of all perspectives>\n"
    )
    user_prompt = f"Token: {token_info}\n\nAgent Analyses:\n{proposal_text}"

    try:
        content, tokens, cost = call_llm(system_prompt, user_prompt, temperature=0.3)
        parsed = parse_agent_response(content)
        return {
            "winner": parsed,
            "strategy": "meta-reasoning",
            "merged": content,
            "tokens": tokens,
            "cost": cost,
        }
    except Exception as e:
        log.warning(f"Meta-reasoning failed, falling back to voting: {e}")
        return resolve_by_voting(proposals)


def resolve_conflict(proposals: List[Dict], token_info: str) -> Dict:
    """
    Main conflict resolution dispatcher.
    Mirrors resolveConflictingProposals() from conflictResolver.ts.
    """
    if len(proposals) <= 1:
        return {
            "winner": proposals[0] if proposals else None,
            "strategy": "none",
            "reasoning": "Single agent, no conflict",
        }

    if not detect_conflict(proposals):
        # No conflict — all agree, return the proposal with highest conviction
        best = max(proposals, key=lambda p: p["conviction"])
        return {
            "winner": best,
            "strategy": "consensus",
            "reasoning": f"All agents agree on {best['signal']}",
        }

    # Conflict detected — use meta-reasoning
    log.info(f"Conflict detected among {len(proposals)} agents, using meta-reasoning")
    result = resolve_by_meta_reasoning(proposals, token_info)
    result["reasoning"] = f"Meta-reasoning synthesis of {len(proposals)} conflicting proposals"
    return result


# ── Consensus logic ──────────────────────────────────────────
def check_consensus(proposals: List[Dict]) -> Optional[Dict]:
    """
    Check if 2+ agents agree on BUY or SELL.
    If so, return the consensus signal dict.
    Returns None if no consensus.
    """
    buy_votes = [p for p in proposals if p["signal"] == "BUY"]
    sell_votes = [p for p in proposals if p["signal"] == "SELL"]
    hold_votes = [p for p in proposals if p["signal"] == "HOLD"]

    buy_count = len(buy_votes)
    sell_count = len(sell_votes)

    # Need 2+ to agree
    if buy_count >= CONSENSUS_THRESHOLD:
        avg_conviction = sum(p["conviction"] for p in buy_votes) / buy_count
        supporting = [p["agent"] for p in buy_votes]
        dissenting = [p["agent"] for p in proposals if p["signal"] != "BUY"]
        return {
            "direction": "BUY",
            "conviction": round(avg_conviction, 3),
            "supporting_agents": supporting,
            "dissenting_agents": dissenting,
            "vote_counts": {"BUY": buy_count, "SELL": sell_count, "HOLD": len(hold_votes)},
            "total_agents": len(proposals),
        }

    if sell_count >= CONSENSUS_THRESHOLD:
        avg_conviction = sum(p["conviction"] for p in sell_votes) / sell_count
        supporting = [p["agent"] for p in sell_votes]
        dissenting = [p["agent"] for p in proposals if p["signal"] != "SELL"]
        return {
            "direction": "SELL",
            "conviction": round(avg_conviction, 3),
            "supporting_agents": supporting,
            "dissenting_agents": dissenting,
            "vote_counts": {"BUY": buy_count, "SELL": sell_count, "HOLD": len(hold_votes)},
            "total_agents": len(proposals),
        }

    # No consensus: either all HOLD or split
    return None


# ── Token source ─────────────────────────────────────────────
def get_active_tokens() -> List[Dict]:
    """
    Fetch active tokens to analyze from multiple sources:
    1. Vantage markets endpoint
    2. Pumpfun signals from DB
    3. Recent orders from DB
    """
    tokens = []
    seen = set()

    # Source 1: Vantage markets
    try:
        req = Request(
            f"{VANTAGE_URL}/api/trading/markets",
            headers={"X-Agent-Key": VANTAGE_KEY},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if isinstance(data, list):
                for m in data:
                    sym = m.get("id", "").upper()
                    if sym and sym not in seen:
                        tokens.append({"symbol": sym, "chain": m.get("chain", "solana"), "source": "vantage_markets"})
                        seen.add(sym)
    except Exception as e:
        log.warning(f"Failed to fetch Vantage markets: {e}")

    # Source 2: Pumpfun signals
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM trading_signals WHERE type='pumpfun' ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()
        conn.close()
        for r in rows:
            sym = r["symbol"].upper() if r["symbol"] else ""
            if sym and sym not in seen:
                tokens.append({"symbol": sym, "chain": "solana", "source": "pumpfun"})
                seen.add(sym)
    except Exception as e:
        log.debug(f"No pumpfun signals: {e}")

    # Source 3: Recent orders (tokens being actively traded)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM trading_orders ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        for r in rows:
            sym = r["symbol"].upper() if r["symbol"] else ""
            if sym and sym not in seen:
                tokens.append({"symbol": sym, "chain": "solana", "source": "recent_orders"})
                seen.add(sym)
    except Exception as e:
        log.debug(f"No recent orders: {e}")

    # Source 4: Hardcoded trending tokens as fallback
    fallback_tokens = [
        {"symbol": "SOL", "chain": "solana", "source": "fallback"},
        {"symbol": "JUP", "chain": "solana", "source": "fallback"},
        {"symbol": "BONK", "chain": "solana", "source": "fallback"},
    ]
    for t in fallback_tokens:
        if t["symbol"] not in seen:
            tokens.append(t)
            seen.add(t["symbol"])

    return tokens


# ── Vantage API ──────────────────────────────────────────────
def post_consensus_signal(token: Dict, consensus: Dict, proposals: List[Dict]) -> bool:
    """
    Post a swarm consensus signal to Vantage /api/trading/signals/ingest.
    """
    payload = {
        "symbol": token["symbol"],
        "direction": consensus["direction"],
        "conviction": consensus["conviction"],
        "chain": token.get("chain", "solana"),
        "source": "swarm_consensus",
        "type": "swarm_consensus",
        "extra": json.dumps({
            "supporting_agents": consensus["supporting_agents"],
            "dissenting_agents": consensus["dissenting_agents"],
            "vote_counts": consensus["vote_counts"],
            "total_agents": consensus["total_agents"],
            "agent_details": [
                {
                    "agent": p["agent"],
                    "perspective": p["perspective"],
                    "signal": p["signal"],
                    "conviction": p["conviction"],
                    "reasoning": p["reasoning"][:200],
                }
                for p in proposals
            ],
        }),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        data = json.dumps(payload).encode()
        req = Request(
            f"{VANTAGE_URL}/api/trading/signals/ingest",
            data=data,
            headers={
                "X-Agent-Key": VANTAGE_KEY,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            log.info(
                f"Consensus signal posted: {token['symbol']} {consensus['direction']} "
                f"(conviction={consensus['conviction']:.2f}, "
                f"votes={consensus['vote_counts']}) → {result.get('status', 'unknown')}"
            )
            if result.get("order_created"):
                log.info(f"  Auto-order created: #{result['order_created']} ({result.get('action', '?')})")
            return True
    except HTTPError as e:
        err_body = e.read().decode()[:200] if e.fp else ""
        log.error(f"Signal post failed HTTP {e.code}: {err_body}")
        return False
    except Exception as e:
        log.error(f"Signal post failed: {e}")
        return False


# ── Main orchestration loop ──────────────────────────────────
def enrich_token_context(token: Dict) -> str:
    """
    Fetch price and market context to give agents real data.
    """
    symbol = token["symbol"]
    chain = token.get("chain", "solana")
    parts = [f"Token: {symbol}", f"Chain: {chain}"]

    # Try to get price from Vantage
    try:
        req = Request(
            f"{VANTAGE_URL}/api/trading/markets/{symbol}/price",
            headers={"X-Agent-Key": VANTAGE_KEY},
        )
        with urlopen(req, timeout=10) as resp:
            price_data = json.loads(resp.read())
            if price_data.get("price"):
                parts.append(f"Current Price: ${price_data['price']:.6f}" if price_data['price'] < 1 else f"Current Price: ${price_data['price']:.2f}")
    except Exception:
        pass

    parts.append(
        "Analyze this cryptocurrency token and determine if it's a BUY, SELL, or HOLD "
        "right now. Consider recent market conditions and your specific analytical "
        "perspective. Be decisive — avoid HOLD unless truly uncertain."
    )
    return "\n".join(parts)


def analyze_token(token: Dict) -> Optional[Dict]:
    """
    Run all 4 agents on a single token. Returns consensus dict or None.
    Mirrors SwarmOrchestrator.orchestrate() from swarm-orchestrator.ts.
    """
    symbol = token["symbol"]
    chain = token.get("chain", "solana")
    log.info(f"Analyzing {symbol} ({chain}) with {len(AGENTS)} perspective agents...")

    token_info = enrich_token_context(token)

    proposals = []
    total_cost = 0.0
    total_tokens = 0

    for agent_def in AGENTS:
        try:
            content, tokens_used, cost = call_llm(
                system_prompt=agent_def["system_prompt"],
                user_prompt=token_info,
                temperature=0.7,
            )
            parsed = parse_agent_response(content)
            parsed["agent"] = agent_def["name"]
            parsed["perspective"] = agent_def["perspective"]
            parsed["tokens"] = tokens_used
            parsed["cost"] = cost

            proposals.append(parsed)
            total_cost += cost
            total_tokens += tokens_used

            log.info(
                f"  {agent_def['name']} ({agent_def['perspective']}): "
                f"{parsed['signal']} (conv={parsed['conviction']:.2f}, "
                f"{tokens_used} tok, ${cost:.4f})"
            )
        except Exception as e:
            log.error(f"  {agent_def['name']} failed: {e}")
            # Add a neutral placeholder
            proposals.append({
                "agent": agent_def["name"],
                "perspective": agent_def["perspective"],
                "signal": "HOLD",
                "conviction": 0.0,
                "reasoning": f"Agent failed: {str(e)[:100]}",
                "tokens": 0,
                "cost": 0,
            })

    if not proposals:
        log.warning(f"No agent results for {symbol}")
        return None

    # Check for consensus
    consensus = check_consensus(proposals)

    if consensus:
        log.info(
            f"✅ CONSENSUS: {symbol} → {consensus['direction']} "
            f"(conviction={consensus['conviction']:.2f}, "
            f"{consensus['vote_counts']['BUY']}B/{consensus['vote_counts']['SELL']}S/"
            f"{consensus['vote_counts']['HOLD']}H)"
        )
        return {
            "token": token,
            "consensus": consensus,
            "proposals": proposals,
            "conflict_resolved": False,
            "total_cost": total_cost,
            "total_tokens": total_tokens,
        }

    # No consensus — need conflict resolution
    log.warning(
        f"⚠️  CONFLICT: {symbol} — "
        f"{sum(1 for p in proposals if p['signal']=='BUY')}B/"
        f"{sum(1 for p in proposals if p['signal']=='SELL')}S/"
        f"{sum(1 for p in proposals if p['signal']=='HOLD')}H — "
        f"resolving..."
    )

    resolution = resolve_conflict(proposals, token_info)
    winner = resolution.get("winner", {})

    if winner and winner.get("signal") in ("BUY", "SELL"):
        avg_conviction = winner.get("conviction", 0.5)
        # Rebuild consensus from resolution
        resolved_consensus = {
            "direction": winner["signal"],
            "conviction": round(avg_conviction, 3),
            "supporting_agents": [winner.get("agent", "meta-reasoning")],
            "dissenting_agents": [
                p["agent"] for p in proposals if p["signal"] != winner["signal"]
            ],
            "vote_counts": {
                "BUY": sum(1 for p in proposals if p["signal"] == "BUY"),
                "SELL": sum(1 for p in proposals if p["signal"] == "SELL"),
                "HOLD": sum(1 for p in proposals if p["signal"] == "HOLD"),
            },
            "total_agents": len(proposals),
        }
        log.info(
            f"🔀 RESOLVED: {symbol} → {resolved_consensus['direction']} "
            f"(via {resolution['strategy']}, conviction={resolved_consensus['conviction']:.2f})"
        )
        return {
            "token": token,
            "consensus": resolved_consensus,
            "proposals": proposals,
            "conflict_resolved": True,
            "resolution_strategy": resolution["strategy"],
            "total_cost": total_cost + resolution.get("cost", 0),
            "total_tokens": total_tokens + resolution.get("tokens", 0),
        }
    else:
        log.info(f"🔒 HOLD: {symbol} — no actionable consensus after resolution")
        return None


# ── Run modes ────────────────────────────────────────────────
_shutdown = False

def handle_shutdown(signum, frame):
    global _shutdown
    log.info("Shutdown signal received, finishing current scan...")
    _shutdown = True


def run_once():
    """Single scan of all active tokens."""
    tokens = get_active_tokens()
    log.info(f"Starting single scan: {len(tokens)} tokens to analyze")

    results = []
    for token in tokens:
        try:
            result = analyze_token(token)
            if result:
                post_consensus_signal(result["token"], result["consensus"], result["proposals"])
                results.append(result)
        except Exception as e:
            log.error(f"Token {token['symbol']} analysis failed: {e}")
            traceback.print_exc()

    log.info(
        f"Scan complete: {len(results)} consensus signals from {len(tokens)} tokens, "
        f"total cost: ${sum(r.get('total_cost', 0) for r in results):.4f}"
    )
    return results


def run_daemon(interval: int = POLL_INTERVAL):
    """Continuous daemon loop with configurable interval."""
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    log.info(f"Ghost Swarm Orchestrator daemon started (interval={interval}s)")
    log.info(f"Using DeepSeek model: {DEEPSEEK_MODEL}")
    log.info(f"Vantage URL: {VANTAGE_URL}")

    scan_count = 0
    total_cost = 0.0
    total_signals = 0

    while not _shutdown:
        scan_count += 1
        log.info(f"=== Scan #{scan_count} ===")

        try:
            tokens = get_active_tokens()
            log.info(f"Tokens to analyze: {len(tokens)}")

            signals_this_scan = 0
            for token in tokens:
                if _shutdown:
                    break
                try:
                    result = analyze_token(token)
                    if result:
                        post_consensus_signal(result["token"], result["consensus"], result["proposals"])
                        signals_this_scan += 1
                        total_cost += result.get("total_cost", 0)
                except Exception as e:
                    log.error(f"Token {token['symbol']} failed: {e}")

            total_signals += signals_this_scan
            log.info(
                f"Scan #{scan_count} done: {signals_this_scan} signals from {len(tokens)} tokens. "
                f"Cumulative: {total_signals} signals, ${total_cost:.4f} total cost"
            )
        except Exception as e:
            log.error(f"Scan #{scan_count} crashed: {e}")
            traceback.print_exc()

        # Wait for next cycle
        if not _shutdown:
            log.debug(f"Sleeping {interval}s until next scan...")
            for _ in range(interval):
                if _shutdown:
                    break
                time.sleep(1)

    log.info(f"Daemon stopped. Total: {scan_count} scans, {total_signals} signals, ${total_cost:.4f}")


# ── CLI ──────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ghost Swarm Orchestrator")
    parser.add_argument(
        "--once", action="store_true", help="Run a single scan and exit"
    )
    parser.add_argument(
        "--daemon", action="store_true", help="Run as a continuous daemon"
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Polling interval in seconds (default: {POLL_INTERVAL})"
    )
    parser.add_argument(
        "--token", type=str, help="Analyze a specific token symbol only"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze but don't post to Vantage"
    )

    args = parser.parse_args()

    if args.token:
        # Single token mode
        token = {"symbol": args.token.upper(), "chain": "solana", "source": "cli"}
        result = analyze_token(token)
        if result:
            print(json.dumps({
                "token": result["token"]["symbol"],
                "direction": result["consensus"]["direction"],
                "conviction": result["consensus"]["conviction"],
                "votes": result["consensus"]["vote_counts"],
                "conflict_resolved": result.get("conflict_resolved", False),
                "resolution_strategy": result.get("resolution_strategy", "consensus"),
                "agents": [
                    {"name": p["agent"], "signal": p["signal"], "conviction": p["conviction"]}
                    for p in result["proposals"]
                ],
                "cost": result.get("total_cost", 0),
            }, indent=2))
            if not args.dry_run:
                post_consensus_signal(result["token"], result["consensus"], result["proposals"])
        else:
            print(json.dumps({"status": "no_consensus", "token": args.token}))
    elif args.daemon:
        run_daemon(args.interval)
    elif args.once:
        run_once()
    else:
        parser.print_help()
