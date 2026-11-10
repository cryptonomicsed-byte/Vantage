"""Round-robin over the shared Helius key pool, from inside Vantage.

The fleet already has a pool: /opt/ares/api_key_pool.py over
/opt/ares/.api_key_pools.json (6 Helius keys) with shared cooldown state in
/opt/ares/.api_key_pool_state.json. Vantage's callers did
``import api_key_pool`` -- which raises ModuleNotFoundError in the service
(cwd /opt/ares/Vantage, /opt/ares not on sys.path), so every call silently
fell back to the single HELIUS_API_KEY and the other keys were never used.
Nothing in those paths reported a 429 back either, so the pool never learnt
a key was capped.

This module loads that pool by file path (no copy of its logic, same state
file, so every daemon agrees which keys are cooling), rotates round-robin
across the keys that are not cooling down, and reports 429 / quota
responses so the next call skips the key.

Flag: HELIUS_POOL_ENABLED (default off -> exactly the old behaviour: the
single HELIUS_API_KEY). Intel paths only; execution keeps its own key.
"""
from __future__ import annotations

import importlib.util
import itertools
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

ARES_HOME = os.environ.get("ARES_HOME", "/opt/ares")
_lock = threading.Lock()
_mod = None
_rr = itertools.count()


def enabled() -> bool:
    return os.environ.get("HELIUS_POOL_ENABLED", "0").lower() in ("1", "true", "yes", "on")


def _pool():
    """The fleet's api_key_pool module, loaded from its file path once."""
    global _mod
    if _mod is None:
        path = os.path.join(ARES_HOME, "api_key_pool.py")
        try:
            spec = importlib.util.spec_from_file_location("ares_api_key_pool", path)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)  # type: ignore[union-attr]
            _mod = m
        except Exception as e:  # noqa: BLE001
            log.warning("helius pool unavailable (%s): %s", path, e)
            _mod = False
    return _mod or None


def _env_key() -> str:
    return os.environ.get("HELIUS_API_KEY", "")


def keys() -> list[str]:
    m = _pool()
    if not m:
        return []
    try:
        return [k for k in m._config().get("helius", {}).get("keys", []) if k]
    except Exception:  # noqa: BLE001
        return []


def available() -> list[str]:
    """Keys not cooling down right now (shared state with every daemon)."""
    m = _pool()
    ks = keys()
    if not m or not ks:
        return []
    try:
        st, now = m._state(), time.time()
        return [k for k in ks if st.get(f"helius:{k}", 0) <= now]
    except Exception:  # noqa: BLE001
        return ks


def get_key(task: str = "vantage") -> str:
    """Next usable key, round-robin. Falls back to HELIUS_API_KEY when the
    pool is disabled or empty; returns "" when every key is cooling down
    (callers then use the public RPC) -- retrying a capped key wastes the
    request and the free-RPC budget the fallback needs."""
    if not enabled():
        return _env_key()
    with _lock:
        av = available()
        if av:
            return av[next(_rr) % len(av)]
    return "" if keys() else _env_key()


def report(key: str, status_code: int, body: str = "") -> None:
    """Tell the shared pool a key failed; 'max usage reached' is a long
    (hard-quota) cooldown, a plain 429 a short one."""
    if not (enabled() and key and not key.startswith("http")):
        return
    if status_code != 429 and not any(p in (body or "").lower() for p in ("max usage", "usage limit", "compute units")):
        return
    m = _pool()
    if m:
        try:
            m.report_error("helius", key, status_code, body or "")
        except Exception as e:  # noqa: BLE001
            log.debug("helius pool report failed: %s", e)


def status() -> dict:
    """Masked pool health for the UI / health endpoint (never the keys)."""
    m = _pool()
    ks = keys()
    av = set(available())
    st = {}
    try:
        st = m._state() if m else {}
    except Exception:  # noqa: BLE001
        pass
    now = time.time()
    return {
        "enabled": enabled(),
        "pool_loaded": bool(m),
        "keys": len(ks),
        "available": len(av),
        "per_key": [{"key": f"…{k[-4:]}", "available": k in av,
                     "cooldown_remaining_s": max(0, round(st.get(f"helius:{k}", 0) - now))} for k in ks],
    }


def url(key: str) -> Optional[str]:
    return f"https://mainnet.helius-rpc.com/?api-key={key}" if key else None
