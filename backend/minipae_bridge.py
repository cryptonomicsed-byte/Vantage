"""Vantage → minipae: trade reasoning as portable memory.

minipae (``/opt/ares/minipae``) stores agent memory as signed, NIP-44 encrypted
engrams (kind 30174) on a Nostr relay. It shipped with no caller in this stack:
decisions were written to the substrate as traces, but the *reasoning* behind a
trade — what the strategy was, what the trigger said, what the executor did —
had no durable, portable home.

This bridge writes one engram per order under ``mem/vantage/order/<id>``.

Identity: a dedicated agent key at ``/root/.ares/minipae_vantage.nsec`` (0600,
created once). It is NOT a trading key and holds no funds; it signs memory only.
The relay is the local vantage-relay (``ws://127.0.0.1:7788``), not a public one,
because trade reasoning is not public data by default. Point ``NIPAE_RELAY`` at
a public relay only if that publication is intended.

Both are configurable:
  VANTAGE_MINIPAE_NSEC   path to the nsec file
  VANTAGE_MINIPAE_RELAY  ws:// target
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, Optional

MINIPAE_REPO = os.environ.get("VANTAGE_MINIPAE_REPO", "/opt/ares/minipae")
NSEC_FILE = os.environ.get("VANTAGE_MINIPAE_NSEC", "/root/.ares/minipae_vantage.nsec")
RELAY = os.environ.get("VANTAGE_MINIPAE_RELAY", "ws://127.0.0.1:7788")
PYTHON = os.environ.get("VANTAGE_MINIPAE_PYTHON", "/opt/ares/venv/bin/python")


def configured() -> bool:
    return os.path.exists(NSEC_FILE) and os.access(NSEC_FILE, os.R_OK)


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    with open(NSEC_FILE) as f:
        env["NIPAE_NSEC"] = f.read().strip()
    env["NIPAE_RELAY"] = RELAY
    return env


def _run(args: list, timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run([PYTHON, "minipae.py", *args], cwd=MINIPAE_REPO,
                          env=_env(), capture_output=True, text=True, timeout=timeout)


def slug_for_order(order_id: Any) -> str:
    return f"mem/vantage/order/{order_id}"


def remember_order(order: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write one order's reasoning as an engram. Returns a report, never raises."""
    if not configured():
        return {"ok": False, "reason": f"no signing key at {NSEC_FILE}"}

    payload = {
        "order_id": order.get("id"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "chain": order.get("chain"),
        "quantity": order.get("quantity"),
        "price": order.get("price"),
        "status": order.get("status"),
        "tx_hash": order.get("tx_hash"),
        "strategy_id": order.get("strategy_id"),
        "trigger_reason": order.get("trigger_reason"),
        "notes": order.get("notes"),
        "created_at": order.get("created_at"),
        "executed_at": order.get("executed_at"),
        "source": "vantage.pipeline",
    }
    if extra:
        payload.update(extra)

    slug = slug_for_order(order.get("id"))
    try:
        proc = _run(["set", slug, json.dumps(payload, default=str)])
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "relay timeout", "slug": slug}
    out = (proc.stdout or "") + (proc.stderr or "")
    ok = "accepted=True" in out
    return {"ok": ok, "slug": slug, "relay": RELAY,
            "detail": out.strip().splitlines()[-1][:200] if out.strip() else ""}


def read(slug: str) -> Dict[str, Any]:
    if not configured():
        return {"ok": False, "reason": f"no signing key at {NSEC_FILE}"}
    try:
        proc = _run(["get", slug], timeout=30)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "relay timeout", "slug": slug}
    value = (proc.stdout or "").strip()
    return {"ok": bool(value) and not value.startswith("no memory"), "slug": slug,
            "value": value[:2000]}
