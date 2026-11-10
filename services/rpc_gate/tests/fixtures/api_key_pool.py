#!/usr/bin/env python3
"""
api_key_pool — shared multi-key rotation for Helius/Birdeye (or any provider)
across every Ares daemon, so one daemon's burst can't drain the quota another
daemon depends on to keep real trading/balance data live.

Two things it does:
  1. DEDICATED KEYS PER TASK — each daemon gets a preferred key index (set in
     the config's "assignments" block) so under normal conditions
     wallet_tracker and pumpfun_wallet_intel are never fighting over the same
     quota bucket at all.
  2. FAILOVER ROTATION — if a task's dedicated key comes back 429 / "usage
     limit exceeded" / "max usage reached", it's marked cooling down and the
     task automatically rotates to the next available key in the pool
     instead of hammering a dead key or silently no-op'ing.

Config: /opt/ares/.api_key_pools.json (create/extend this file with your
additional account keys — see bottom of this file for the format). Cooldown
state persists in /opt/ares/.api_key_pool_state.json so a cooldown survives
daemon restarts.

Usage:
    from api_key_pool import get_key, report_error

    key = get_key("helius", "wallet_tracker")       # dedicated-first, rotates on cooldown
    ...
    if resp.status_code in (429, 400) and "usage" in resp.text.lower():
        report_error("helius", key, resp.status_code, resp.text)
"""
import json, os, time, fcntl

CONFIG_PATH = "/opt/ares/.api_key_pools.json"
STATE_PATH = "/opt/ares/.api_key_pool_state.json"

# How long a key sits out after each kind of failure. A hard quota message
# ("usage limit exceeded", "max usage reached") is a plan-level cap that
# usually doesn't reset in minutes, so it gets a long cooldown; a plain 429
# is more likely a transient burst limit and gets a short one.
COOLDOWN_HARD_QUOTA = 3600     # 1 hour
COOLDOWN_RATE_LIMIT = 120      # 2 minutes
QUOTA_PHRASES = ("usage limit exceeded", "max usage reached", "compute units")


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _config():
    return _load_json(CONFIG_PATH, {"helius": {"keys": [], "assignments": {}}, "birdeye": {"keys": [], "assignments": {}}})


def _state():
    return _load_json(STATE_PATH, {})  # "{provider}:{key}" -> cooldown_until epoch


def get_key(provider: str, task: str) -> str:
    """Best available key for this provider+task: the task's dedicated key
    if it's not cooling down, else the first non-cooling-down key in the
    pool, else the dedicated key anyway (better to retry a cooling-down key
    than return nothing and silently no-op)."""
    cfg = _config().get(provider, {})
    keys = cfg.get("keys", [])
    if not keys:
        return ""  # caller should fall back to its own env var if the pool is empty
    state = _state()
    now = time.time()

    def cooling(k):
        return state.get(f"{provider}:{k}", 0) > now

    preferred_idx = cfg.get("assignments", {}).get(task)
    if preferred_idx is not None and 0 <= preferred_idx < len(keys):
        preferred = keys[preferred_idx]
        if not cooling(preferred):
            return preferred

    for k in keys:
        if not cooling(k):
            return k

    # everything's cooling down — return the dedicated key (or first key) anyway
    return keys[preferred_idx] if preferred_idx is not None and preferred_idx < len(keys) else keys[0]


def report_error(provider: str, key: str, status_code: int, body: str = ""):
    """Call this when a request with `key` fails with a rate-limit/quota
    error, so future get_key() calls skip it until the cooldown expires."""
    if not key:
        return
    body_l = (body or "").lower()
    is_hard_quota = any(p in body_l for p in QUOTA_PHRASES)
    cooldown = COOLDOWN_HARD_QUOTA if is_hard_quota else COOLDOWN_RATE_LIMIT
    if status_code not in (429, 400, 402, 403):
        return
    state = _state()
    state[f"{provider}:{key}"] = time.time() + cooldown
    _save_json_atomic(STATE_PATH, state)


def pool_status(provider: str) -> list[dict]:
    """For diagnostics — which keys exist, which are cooling down, for how long."""
    cfg = _config().get(provider, {})
    keys = cfg.get("keys", [])
    state = _state()
    now = time.time()
    out = []
    for i, k in enumerate(keys):
        until = state.get(f"{provider}:{k}", 0)
        out.append({
            "index": i, "key_suffix": k[-6:] if len(k) > 6 else k,
            "cooling_down": until > now,
            "cooldown_remaining_s": max(0, round(until - now)),
        })
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        for provider in ("helius", "birdeye"):
            print(f"\n{provider}:")
            for row in pool_status(provider):
                flag = f"COOLING ({row['cooldown_remaining_s']}s left)" if row["cooling_down"] else "available"
                print(f"  [{row['index']}] ...{row['key_suffix']}  {flag}")
    else:
        print(__doc__)
        print("\nCurrent config:", CONFIG_PATH)
        print("Run `python3 api_key_pool.py status` to see per-key cooldown state.")
