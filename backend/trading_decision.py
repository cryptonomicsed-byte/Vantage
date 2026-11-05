"""
Trading decision branch. Thin adapter over system_one.decide().
Keeps jev_decide() signature for backward compatibility.
"""
import os
try:
    from .system_one import decide, NOUL, CHOICE, SCORE
except ImportError:  # backend/ itself on sys.path
    from system_one import decide, NOUL, CHOICE, SCORE

CONFIDENCE_THRESHOLD = float(os.environ.get("JEV_CONFIDENCE_THRESHOLD", "0.65"))


async def jev_decide(signal: dict) -> dict:
    """
    Thin adapter: signal dict -> {verdict, confidence, reasoning}.
    Preserves the public jev_decide() signature for existing pipeline callers.
    Falls back to legacy jev_decision logic on system_one failure.
    """
    state = {
        "question": signal.get("question", ""),
        "yes_price": signal.get("yes_price", 0.5),
        "volume_24h": signal.get("volume_24h", 0),
        "source": signal.get("source", "unknown"),
        "end_date": signal.get("end_date", "unknown"),
        # Loom enrichment if available
        "loom_conviction": signal.get("loom_conviction"),
        "loom_direction": signal.get("loom_direction"),
        "strategy_lab_score": signal.get("strategy_lab_score"),
    }

    questions = {
        "direction": {
            "type": CHOICE,
            "options": ["up", "down", "pass"],
            "description": "Which direction to bet, or pass if insufficient signal",
        },
        "conviction": {
            "type": SCORE,
            "options": ["none", "low", "medium", "high"],
            "description": "Conviction level in the direction",
        },
        "should_trade": {
            "type": NOUL,
            "description": "Should this signal be acted on?",
        },
    }

    result = await decide(state, questions)

    if result.get("error") or not result.get("answers"):
        # fall back to legacy logic
        return await _legacy_decide(signal)

    answers = result["answers"]
    direction_ans = answers.get("direction", {})
    conviction_ans = answers.get("conviction", {})
    should_trade_ans = answers.get("should_trade", {})

    direction_val = direction_ans.get("value", "pass")
    direction_prob = float(direction_ans.get("probability", 0.0))
    conviction_val = conviction_ans.get("value", "none")
    should_trade_prob = float(should_trade_ans.get("probability", 0.0))
    should_trade_val = should_trade_ans.get("value", False)

    # map direction to verdict
    if direction_val == "up":
        verdict = "yes"
    elif direction_val == "down":
        verdict = "no"
    else:
        verdict = "skip"

    confidence = should_trade_prob

    # apply threshold gate
    if confidence < CONFIDENCE_THRESHOLD:
        verdict = "skip"

    reasoning = (
        f"direction={direction_val}({direction_prob:.2f}) "
        f"conviction={conviction_val} "
        f"should_trade={should_trade_val}({confidence:.2f}) "
        f"model={result.get('model_used', '?')}"
    )

    return {"verdict": verdict, "confidence": confidence, "reasoning": reasoning}


async def _legacy_decide(signal: dict) -> dict:
    """Fallback: original jev_decision.py logic reproduced inline."""
    import os
    import json
    import httpx

    OPENROUTER_BASE = os.environ.get(
        "OPENROUTER_BASE", os.environ.get("OPENROUTER_BASE_URL", "http://127.0.0.1:8300/v1")
    )
    JEV_MODEL = os.environ.get("JEV_MODEL", os.environ.get("OPENROUTER_MODEL", "free-reasoner"))

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        try:
            with open("/opt/ares/.omniroute_key_swarm_orchestrator") as fh:
                api_key = fh.read().strip()
        except Exception:
            pass
    if not api_key:
        return {"verdict": "skip", "confidence": 0.0, "reasoning": "no api key (legacy fallback)"}

    prompt = (
        f"You are Jev, a prediction market analyst. Analyze this market and decide if it is worth signaling.\n\n"
        f"Market: {signal.get('question', 'Unknown')}\n"
        f"Yes Price: {signal.get('yes_price', 0.5):.1%}\n"
        f"Volume 24h: ${signal.get('volume_24h', 0):,.0f}\n"
        f"Source: {signal.get('source', 'unknown')}\n"
        f"End Date: {signal.get('end_date', 'unknown')}\n\n"
        "Respond with JSON only: {{\"verdict\": \"yes\" or \"no\" or \"skip\", \"confidence\": 0.0-1.0, \"reasoning\": \"brief explanation\"}}\n"
        "verdict=yes means bet YES, no means bet NO, skip means insufficient signal."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": JEV_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": int(os.environ.get("OPENROUTER_MAX_TOKENS", "1024")),
                },
            )
        if r.status_code != 200:
            return {"verdict": "skip", "confidence": 0.0, "reasoning": f"legacy api error {r.status_code}"}
        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1].lstrip("json").strip()
        result = json.loads(content)
        result["confidence"] = float(result.get("confidence", 0.0))
        result["reasoning"] = "[legacy] " + result.get("reasoning", "")
        return result
    except Exception as e:
        return {"verdict": "skip", "confidence": 0.0, "reasoning": f"[legacy] {e}"}
