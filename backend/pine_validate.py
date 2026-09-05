"""Compile-check agent-authored Pine against TradingView's own Pine compiler.

# Why this exists

`routers/pine.py` gates scripts through `_review()`, a Zàngbétò governance call
that **fails open**: with `ZANGBETO_URL` unset — its default — every script is
approved without inspection. The sandbox still bounds *execution*, so that is a
governance gap rather than a security hole: nothing currently judges whether an
agent's script is even coherent before it is run, saved or shared into a guild.

A compile check is the one gate that can fail **closed** without a service
dependency. A compile error is unambiguous, deterministic, and needs a single
unauthenticated POST. TradingView exposes its real compiler at
`pine-facade.tradingview.com`, which returns structured errors with exact line
and column — no login, no cookie, no browser.

# The distinction this module is built around

Two outcomes look alike from the caller's side and must never be conflated:

* **The compiler answered, and the script is broken.** That is a verdict. The
  caller rejects the script (`422`).
* **We could not reach the compiler.** That is not a verdict. The caller must
  proceed as it did before this module existed, because a network outage that
  silently began rejecting every agent's work would be far worse than the gap
  it was added to close.

`status` carries that distinction: `"ok"` means a real verdict is present,
`"unavailable"` means there is none. `valid` is meaningful only when
`status == "ok"`.

# Two validators, answering different questions

There are two, and the order is deliberate:

1. **The native engine** (`pine-runtime`, `POST /validate`) answers *can Vantage
   run this?* It is a localhost sidecar, so it needs no internet and cannot be
   taken away by a third party.
2. **TradingView** answers *is this valid Pine Script?*

The native check is the one that actually matters here, and it is checked
first. TradingView accepts the whole Pine language while `pine-engine.js`
implements a subset — so `ta.percentrank`, `ta.linreg` offsets and dozens of
other constructs compile cleanly at TradingView and then fail inside our own
sandbox. A gate that only asked TradingView would wave those through and the
failure would surface later, at run time, as a sandbox error the author cannot
connect to anything.

TradingView stays as a second opinion because the reverse also happens: the
native parser is smaller, so it can accept something malformed that a real Pine
compiler would reject. Two gates, cheapest and most relevant first, and a
verdict from either is enough to refuse.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# Overridable so a self-hosted or mocked compiler can stand in, and so tests
# never depend on reaching TradingView.
FACADE_URL = os.environ.get(
    "PINE_FACADE_URL",
    "https://pine-facade.tradingview.com/pine-facade/translate_light/",
)
# The native engine sidecar. Same default as routers/pine.py uses for /run.
RUNTIME_URL = os.environ.get("PINE_RUNTIME_URL", "http://127.0.0.1:9871")
# Set to "0" to consult TradingView only. The native check needs no network
# beyond localhost, so there is rarely a reason to.
NATIVE_ENABLED = os.environ.get("PINE_VALIDATE_NATIVE", "1") != "0"
# Set to "0" to skip validation entirely and restore the previous behaviour.
ENABLED = os.environ.get("PINE_VALIDATE_ENABLED", "1") != "0"
TIMEOUT = float(os.environ.get("PINE_VALIDATE_TIMEOUT", "6.0"))

# pine-facade sits behind nginx that rejects bare API clients; Origin, Referer
# and a browser User-Agent are the whole handshake. No credential is sent, and
# none should ever be added here — this endpoint is used precisely because it
# needs no account.
_HEADERS = {
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

# Authoring is a tight loop — an agent recompiles the same script repeatedly
# while tuning one number — so identical source is answered from memory. Only
# real verdicts are cached; an unavailable compiler must be retried, never
# remembered.
_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL = float(os.environ.get("PINE_VALIDATE_CACHE_TTL", "900"))
CACHE_MAX = 512


def _cache_key(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: str, val: dict) -> None:
    if len(_CACHE) >= CACHE_MAX:
        # Evict the oldest. The map is small and this runs rarely, so an ordered
        # scan is cheaper than maintaining a second index.
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time(), val)


def annotate(code: str, errors: list[dict]) -> str:
    """Render the offending source lines with the compiler's complaint attached.

    The agent that wrote the script is the one that has to fix it, so the error
    it receives shows the line rather than only naming its number.
    """
    lines = code.splitlines()
    chunks = []
    for err in errors:
        ln = err.get("line")
        src = lines[ln - 1] if isinstance(ln, int) and 1 <= ln <= len(lines) else "?"
        chunks.append(f"{ln:>4} | {src}\n     |  ^-- {err.get('message', '')}")
    return "\n".join(chunks)


def _parse(payload: dict, code: str) -> dict:
    """Turn a pine-facade reply into our verdict shape.

    Clean source omits `errors2` entirely; broken source carries one entry per
    diagnostic with `start.line` / `start.column`.
    """
    if not payload.get("success"):
        # The compiler declined to answer at all (malformed request, upstream
        # change). That is an absent verdict, not a failing script.
        return {"status": "unavailable", "reason": "pine-facade rejected the request"}

    raw = (payload.get("result") or {}).get("errors2") or []
    errors = [
        {
            "line": (e.get("start") or {}).get("line"),
            "column": (e.get("start") or {}).get("column"),
            "message": e.get("message", ""),
        }
        for e in raw
    ]
    out: dict = {"status": "ok", "valid": not errors, "errors": errors}
    if errors:
        out["annotated"] = annotate(code, errors)
    return out


async def _post(code: str):
    """Send one compile request. The single seam this module talks through.

    Deliberately its own function rather than an inline `httpx.AsyncClient`:
    `routers/pine.py` also holds a reference to the `httpx` module, and anything
    that swaps `httpx.AsyncClient` to stand in for the Pine *sandbox* would
    otherwise intercept this compile call too — the same module object serving
    two unrelated purposes. Callers that need to stand in for the compiler
    replace `_post`, and the two stay independent.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        return await c.post(FACADE_URL, headers=_HEADERS, data={"source": code})


async def validate_pine(code: str, *, use_cache: bool = True) -> dict:
    """Compile-check `code`. Never raises.

    Returns one of:
        {"status": "ok", "valid": True,  "errors": []}
        {"status": "ok", "valid": False, "errors": [{line, column, message}...],
         "annotated": "..."}
        {"status": "unavailable", "reason": "..."}
    """
    if not ENABLED:
        return {"status": "unavailable", "reason": "validation disabled"}
    if not isinstance(code, str) or not code.strip():
        # An empty script is the caller's own input error, not a compiler
        # verdict; the route already rejects it before reaching here.
        return {"status": "unavailable", "reason": "empty script"}

    key = _cache_key(code)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    try:
        r = await _post(code)
        if r.status_code != 200:
            return {"status": "unavailable", "reason": f"pine-facade HTTP {r.status_code}"}
        verdict = _parse(r.json(), code)
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as e:
        # Unreachable, timed out, or answered with something that is not JSON.
        logger.debug("pine-facade unavailable: %s", e)
        return {"status": "unavailable", "reason": f"{type(e).__name__}"}

    if use_cache and verdict.get("status") == "ok":
        _cache_put(key, verdict)
    return verdict


def summarize(verdict: dict, limit: int = 3) -> str:
    """One-line human summary for an HTTP error detail.

    Long scripts can produce dozens of diagnostics; the first few are what the
    author needs, and the count tells them whether to expect more.
    """
    errors = verdict.get("errors") or []
    if not errors:
        return "script failed to compile"
    head = "; ".join(
        f"line {e.get('line')}: {e.get('message')}" for e in errors[:limit]
    )
    extra = len(errors) - limit
    return head + (f" (+{extra} more)" if extra > 0 else "")


async def _post_native(code: str):
    """Ask the local engine whether it can run `code`. Its own seam, like `_post`."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        return await c.post(f"{RUNTIME_URL.rstrip('/')}/validate", json={"script": code})


async def validate_native(code: str) -> dict:
    """Can the Vantage sandbox actually execute this script?

    Same verdict shape as `validate_pine`, so a caller handles both identically.
    An unreachable sidecar is `unavailable` — no verdict — for the same reason
    an unreachable TradingView is: the gate must not start refusing every script
    because a service is down.
    """
    if not NATIVE_ENABLED:
        return {"status": "unavailable", "reason": "native validation disabled"}
    if not isinstance(code, str) or not code.strip():
        return {"status": "unavailable", "reason": "empty script"}
    try:
        r = await _post_native(code)
        if r.status_code != 200:
            return {"status": "unavailable", "reason": f"pine-runtime HTTP {r.status_code}"}
        payload = r.json()
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as e:
        logger.debug("pine-runtime unavailable: %s", e)
        return {"status": "unavailable", "reason": f"{type(e).__name__}"}

    errors = [
        {
            "line": e.get("line"),
            "column": None,
            "message": e.get("message", ""),
            "source": e.get("source"),
        }
        for e in (payload.get("errors") or [])
    ]
    out: dict = {
        "status": "ok",
        "valid": bool(payload.get("valid")),
        "errors": errors,
        "validator": "native",
    }
    if errors:
        out["annotated"] = annotate(code, errors)
    return out


async def validate_all(code: str) -> dict:
    """Run both gates, native first, and return the first real verdict of
    *invalid* — or the best available verdict if the script passes.

    Ordering is the point. The native engine decides whether we can run the
    script at all, which is the question this codebase actually needs answered,
    and it costs a localhost round trip. TradingView is consulted only for a
    script the sandbox already accepts, to catch Pine errors a smaller parser
    might have let through.

    If both are unavailable the result is `unavailable` and the caller proceeds,
    exactly as before either gate existed.
    """
    native = await validate_native(code)
    if native.get("status") == "ok" and not native.get("valid"):
        return native

    remote = await validate_pine(code)
    if remote.get("status") == "ok" and not remote.get("valid"):
        remote["validator"] = "tradingview"
        return remote

    # Neither refused. Prefer whichever actually produced a verdict.
    if native.get("status") == "ok":
        return native
    if remote.get("status") == "ok":
        remote["validator"] = "tradingview"
        return remote
    return {
        "status": "unavailable",
        "reason": f"native: {native.get('reason')}; tradingview: {remote.get('reason')}",
    }
