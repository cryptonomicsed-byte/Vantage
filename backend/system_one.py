"""
Shared System One decision client.
Emulates the TypeSafe System One wire format via deepseek-chat on the omnirouter.
Wire format:
  POST /v1/chat/completions with response_format: {type: json_object}
  Prompt encodes: state dict + questions dict (noul/choice/score types)
  Response: {answers: {q_name: {type, value, probability}}, model_used, latency_ms}

Usage:
  result = await decide(state, questions)
  result["answers"]["direction"]["value"]  # "up"/"down"/"pass"
  result["answers"]["should_trade"]["probability"]  # 0.73
"""
import os
import json
import time

SYSTEM_ONE_BASE = os.environ.get(
    "SYSTEM_ONE_BASE",
    os.environ.get("OPENROUTER_BASE", "http://127.0.0.1:8300/v1"),
)
SYSTEM_ONE_MODEL = os.environ.get("SYSTEM_ONE_MODEL", "deepseek-chat")
SYSTEM_ONE_FALLBACK = os.environ.get("SYSTEM_ONE_FALLBACK", "free-reasoner")

# Question types
NOUL = "noul"      # calibrated P(true), 0.0-1.0
CHOICE = "choice"  # discrete option from a list
SCORE = "score"    # ordered level from a list (e.g. none/low/medium/high)

_SYSTEM_PROMPT = (
    "You are System One, a fast probabilistic decision engine.\n\n"
    "You receive a JSON state describing a situation, and a JSON questions object.\n"
    "For each question you must return a calibrated answer.\n\n"
    "Question type rules:\n"
    "- noul: return {type: \"noul\", value: true|false, probability: 0.0-1.0}\n"
    "  probability = P(value is true); if value=false, probability = 1-P(true)\n"
    "- choice: return {type: \"choice\", value: \"<one of the listed options>\", probability: 0.0-1.0}\n"
    "  probability = confidence in that choice\n"
    "- score: return {type: \"score\", value: \"<one of the listed levels>\", probability: 0.0-1.0}\n"
    "  probability = confidence in that level\n\n"
    "Respond with ONLY a JSON object shaped:\n"
    "{\n"
    "  \"answers\": {\n"
    "    \"<question_name>\": {<type, value, probability>},\n"
    "    ...\n"
    "  }\n"
    "}\n\n"
    "No extra keys, no explanation, no markdown. Pure JSON only."
)


def _build_user_message(state: dict, questions: dict) -> str:
    lines = ["STATE:", json.dumps(state, indent=2), "", "QUESTIONS:"]
    for q_name, q_def in questions.items():
        q_type = q_def.get("type", "noul")
        desc = q_def.get("description", "")
        opts = q_def.get("options", [])
        if opts:
            lines.append(f"  {q_name} ({q_type}, options={opts}): {desc}")
        else:
            lines.append(f"  {q_name} ({q_type}): {desc}")
    return "\n".join(lines)


def _read_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    try:
        with open("/opt/ares/.omniroute_key_swarm_orchestrator") as f:
            return f.read().strip()
    except Exception:
        return ""


async def decide(
    state: dict,
    questions: dict,
    model_override: str | None = None,
) -> dict:
    """
    state: arbitrary JSON describing the situation
    questions: {q_name: {"type": "noul"|"choice"|"score", "options": [...], "description": str}}
    Returns: {answers: {q_name: {type, value, probability}}, model_used, latency_ms, raw}
    """
    import httpx

    api_key = _read_api_key()
    user_msg = _build_user_message(state, questions)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    models_to_try: list[str] = []
    if model_override:
        models_to_try = [model_override]
    else:
        models_to_try = [SYSTEM_ONE_MODEL, SYSTEM_ONE_FALLBACK]

    last_error: str | None = None
    for model in models_to_try:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{SYSTEM_ONE_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "response_format": {"type": "json_object"},
                        "messages": messages,
                        "temperature": 0.05,
                        "max_tokens": 512,
                    },
                )
            latency_ms = int((time.monotonic() - t0) * 1000)
            if r.status_code != 200:
                last_error = f"api error {r.status_code}: {r.text[:200]}"
                continue
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("```")[1].lstrip("json").strip()
            parsed = json.loads(content)
            answers = parsed.get("answers", parsed)
            return {
                "answers": answers,
                "model_used": model,
                "latency_ms": latency_ms,
                "raw": content,
            }
        except Exception as e:
            last_error = str(e)
            continue

    return {
        "answers": {},
        "error": last_error or "unknown error",
        "model_used": None,
        "latency_ms": 0,
    }
