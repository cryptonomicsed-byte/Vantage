"""Shared signal-posting client for Vantage daemons.

Every daemon in this directory that produces signals had grown its own copy of
the same three-line urllib POST, and each copy had drifted. The drift was not
cosmetic:

  * **Auth.** The ingest endpoints require system-tool auth (`get_system_tool`),
    but eighteen daemons posted `X-Agent-Key`. That is a 401 on every call, so
    the signal pipeline has been dead in production -- silently, because most
    of these daemons swallow the exception and print a line.

  * **Conviction scale.** Conviction is a 0-1 confidence platform-wide, and
    above 0.7 the trading ingest endpoint *auto-creates a real order*. Several
    daemons used 0-5 or 0-7 scales. While auth was broken this was harmless;
    the moment auth is fixed, an unnormalised scale means every signal clears
    the execution threshold. Fixing auth alone would have switched on
    auto-trading across the fleet, which is why both are fixed together.

  * **Routing.** `/api/intel/signals/ingest` scores and pools a signal.
    `/api/trading/signals/ingest` can place an order. Daemons that merely
    observe -- balance readers, threat scanners, analytics -- were posting to
    the executing endpoint because it was the one the neighbouring file used.

So: one client, safe by default.

Routing is the important default. `post_signal()` goes to the intel pool
unless the caller passes `execute=True` *and* the operator has set
VANTAGE_DAEMON_AUTO_EXECUTE=1. Restoring auth on a machine that has been
running these daemons for months should not start spending money because a
header was corrected; enabling that is a deliberate, separate act. When a
daemon asks to execute and the switch is off, the signal is still delivered --
to the intel pool -- and the downgrade is logged rather than dropped.
"""
from __future__ import annotations

import json
import logging
import math
import os
import urllib.error
import urllib.request

logger = logging.getLogger("vantage_signals")

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")

INTEL_INGEST = "/api/intel/signals/ingest"
TRADING_INGEST = "/api/trading/signals/ingest"

# Above this, the trading endpoint creates a real order for a directional
# signal. Mirrored here only so daemons can reason about their own output;
# the server remains the authority.
AUTO_EXECUTION_THRESHOLD = 0.7


def auto_execute_enabled() -> bool:
    """Whether daemons may post to the order-creating endpoint.

    Read at call time, not import time, so a test or an operator can flip it
    without restarting the process.
    """
    return os.environ.get("VANTAGE_DAEMON_AUTO_EXECUTE", "").strip().lower() in ("1", "true", "yes", "on")


def _tool_key(tool: str) -> str:
    """The system-tool key for `tool`, from the env.

    Per-tool first (VANTAGE_TOOL_INTEL_KEY), then a shared fallback, matching
    how ares_alpha_hunter.py and polymarket_trader.py already read theirs.
    """
    return (
        os.environ.get(f"VANTAGE_TOOL_{tool.upper()}_KEY")
        or os.environ.get(f"VANTAGE_TOOL_{tool.upper()}")
        or os.environ.get("VANTAGE_TOOL_KEY")
        or ""
    )


def headers_for(tool: str) -> dict:
    """System-tool auth headers. `X-Agent-Key` does not authenticate these
    endpoints -- an agent key gets a 401, which is how most of this directory
    originally shipped."""
    return {
        "Content-Type": "application/json",
        "X-Vantage-Tool": tool,
        "X-Vantage-Tool-Key": _tool_key(tool),
    }


def normalise_conviction(value, scale: float = 1.0) -> float:
    """Coerce a daemon's confidence to the platform's 0-1 contract.

    `scale` is the maximum of the caller's own range: a daemon scoring 0-7
    passes scale=7.0 and gets a true proportion back, rather than everything
    above 0.7 being flattened to 1.0 by a clamp. A clamp is the wrong repair
    for a scale mismatch -- it would turn a mediocre 2-out-of-7 into a
    maximum-confidence, auto-executing signal.

    Junk (None, strings, NaN, inf) becomes 0.0 rather than raising: a bad
    reading from one of thirty upstream APIs should not kill a daemon loop,
    and 0.0 is the reading that causes nothing to happen.
    """
    if scale <= 0:
        raise ValueError(f"scale must be positive, got {scale!r}")
    try:
        raw = float(value)
    except (TypeError, ValueError):
        logger.warning("non-numeric conviction %r treated as 0.0", value)
        return 0.0
    if math.isnan(raw) or math.isinf(raw):
        logger.warning("non-finite conviction %r treated as 0.0", value)
        return 0.0
    return max(0.0, min(1.0, raw / scale))


def post_signal(
    symbol: str,
    source: str,
    *,
    tool: str = "intel",
    type_: str = "signal",
    conviction=0.5,
    scale: float = 1.0,
    direction: str = "",
    detail: str = "",
    mint: str = "",
    chain: str = "",
    agent_id=None,
    execute: bool = False,
    timeout: int = 10,
) -> dict | None:
    """Post one signal. Returns the decoded response, or None on failure.

    Never raises: these run in `while True` loops where an unhandled error from
    one of thirty upstream feeds takes the whole daemon down.
    """
    conviction = normalise_conviction(conviction, scale)

    to_trading = execute and auto_execute_enabled()
    if execute and not to_trading:
        logger.info(
            "%s: routing %s to the intel pool -- auto-execution is off "
            "(set VANTAGE_DAEMON_AUTO_EXECUTE=1 to allow order creation)",
            source, symbol,
        )

    if to_trading:
        if agent_id is None:
            # The trading endpoint requires it (400 otherwise), and there is no
            # sane default: guessing an agent id would place someone else's order.
            logger.error("%s: agent_id is required to post %s to the trading endpoint", source, symbol)
            return None
        endpoint, tool_name = TRADING_INGEST, "trading"
        payload = {
            "symbol": symbol, "source": source, "direction": direction,
            "conviction": conviction, "agent_id": agent_id,
            "chain": chain or "solana", "detail": detail,
        }
    else:
        endpoint, tool_name = INTEL_INGEST, tool
        payload = {
            "symbol": symbol, "source": source, "type": type_,
            "conviction": conviction, "direction": direction, "detail": detail,
        }
        if mint:
            payload["mint"] = mint

    request = urllib.request.Request(
        f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(payload, default=str).encode(),
        headers=headers_for(tool_name),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # Surface the body. A 401 here means the tool key is missing or wrong,
        # and the whole reason this pipeline sat dead was that the failure was
        # indistinguishable from "nothing to report".
        body = ""
        try:
            body = exc.read().decode()[:300]
        except Exception:
            pass
        logger.warning("%s: %s %s -> %s %s", source, endpoint, symbol, exc.code, body)
    except Exception as exc:
        logger.warning("%s: %s %s failed: %s", source, endpoint, symbol, exc)
    return None
