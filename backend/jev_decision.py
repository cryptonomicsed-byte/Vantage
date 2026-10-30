"""Jev decision engine: takes a prediction_signal -> OpenRouter LLM -> returns verdict + confidence."""
import os, json
from typing import Optional

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
JEV_MODEL = os.environ.get("JEV_MODEL", "anthropic/claude-haiku-4-5")
CONFIDENCE_THRESHOLD = float(os.environ.get("JEV_CONFIDENCE_THRESHOLD", "0.65"))

async def jev_decide(signal: dict) -> dict:
    """Pure function: signal dict -> {verdict, confidence, reasoning}. No side effects."""
    import httpx
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"verdict": "skip", "confidence": 0.0, "reasoning": "no api key configured"}

    prompt = f"""You are Jev, a prediction market analyst. Analyze this market and decide if it is worth signaling.

Market: {signal.get("question", "Unknown")}
Yes Price: {signal.get("yes_price", 0.5):.1%}
Volume 24h: ${signal.get("volume_24h", 0):,.0f}
Source: {signal.get("source", "unknown")}
End Date: {signal.get("end_date", "unknown")}

Respond with JSON only: {{"verdict": "yes" or "no" or "skip", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}
verdict=yes means bet YES, no means bet NO, skip means insufficient signal."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": JEV_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
            )
            if r.status_code != 200:
                return {"verdict": "skip", "confidence": 0.0, "reasoning": f"api error {r.status_code}"}
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("```")[1].lstrip("json").strip()
            result = json.loads(content)
            result["confidence"] = float(result.get("confidence", 0.0))
            return result
    except Exception as e:
        return {"verdict": "skip", "confidence": 0.0, "reasoning": str(e)}
