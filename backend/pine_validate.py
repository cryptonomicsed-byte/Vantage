import hashlib
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[dict, float]] = {}
_TTL = 300
_ENDPOINT = "https://pine-facade.tradingview.com/pine-facade/translate/"


def _cached(key: str) -> dict | None:
    entry = _CACHE.get(key)
    if entry and time.monotonic() - entry[1] < _TTL:
        return entry[0]
    return None


def _store(key: str, result: dict) -> None:
    _CACHE[key] = (result, time.monotonic())


async def validate_pine(script: str) -> dict:
    if not script or not script.strip():
        return {"valid": False, "errors": [{"line": 0, "column": 0, "message": "empty script"}]}

    key = hashlib.sha256(script.encode()).hexdigest()
    cached = _cached(key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(
                _ENDPOINT,
                content=script,
                headers={"Content-Type": "text/plain"},
            )
        data = r.json()
        inner = data.get("result", {})
        if inner.get("ok", True):
            result: dict = {"valid": True}
        else:
            raw_errors = inner.get("errors") or []
            errors = [
                {
                    "line": e.get("start", {}).get("line", 0) + 1,
                    "column": e.get("start", {}).get("character", 0) + 1,
                    "end_line": e.get("end", {}).get("line", 0) + 1,
                    "end_column": e.get("end", {}).get("character", 0) + 1,
                    "message": e.get("message", ""),
                }
                for e in raw_errors
            ]
            result = {"valid": False, "errors": errors}
    except Exception as exc:
        logger.debug("pine_validate: network error: %s", exc)
        return {"valid": True, "network_error": True}

    _store(key, result)
    return result


def annotate(script: str, errors: list[dict]) -> str:
    lines = script.splitlines()
    insertions: dict[int, list[str]] = {}
    for e in errors:
        line_idx = max(0, e.get("line", 1) - 1)
        insertions.setdefault(line_idx, []).append(f"// \u26a0 {e['message']}")

    out: list[str] = []
    for i, line in enumerate(lines):
        for comment in insertions.get(i, []):
            out.append(comment)
        out.append(line)
    return "\n".join(out)
