"""Paper/live mode — one source of truth.

The rules this module encodes:

  PAPER IS ALWAYS ON.  Nothing here can turn paper trading off, and nothing in
  the forecast/decision path consults an approval record to decide whether it
  may run. Signals, scores, decisions and paper outcomes are generated and
  recorded unconditionally.

  LIVE IS OFF UNLESS EVERY LAYER PERMITS IT (fork is at EXECUTION, not at
  signal generation: one signal pipeline, two executors, paper always runs):

    Layer 0  instance ceiling: VANTAGE_LIVE_TRADING must be explicitly truthy.
             Unset or falsy = brake. It is a ceiling, never a force-on: with the
             ceiling open the operator switch (live_mode, changed only through a
             Sentinel sign-off) still decides.
    Layer 1  per-strategy opt-in, default off: the strategy is `armed`, `live`,
             AND bound to a wallet with a key. Unbind or lose the wallet and the
             strategy is paper again.
    Layer 2  the owning account's kill switch (Settings > Security). Engaged =
             every strategy of that account is paper, whatever its toggles say.

  DEMO MODE (VANTAGE_DEMO_MODE=true) is a separate state, not a switch value:
  the deployment is labelled demo, the Layer 0 ceiling reads as open so the
  live path can be exercised end to end, and every point that would settle on
  a real chain (sign, broadcast, Sui settle) hard-refuses. A live switch set
  while in demo is tagged and reads as OFF once the instance leaves demo.

  and even then a real order only executes when the owning agent calls
  POST /orders/{id}/execute-live with its own X-Agent-Key, because wallet keys
  are encrypted against each agent's plaintext key and no daemon can sign.

  RS-2 (the resolver/policy approval gate) is NOT part of this module and is
  not referenced anywhere in Vantage. It lives outside the repo, in the
  forecast-gate tooling. If it is ever wired in, it belongs on the LIVE branch
  only — see `rs2_applies_to()` below, which exists to make that explicit
  rather than to enforce anything.

Every mode transition is written to `trading_mode_audit` with the actor, the
scope, the previous state and the new state, so "who armed this, when, and for
what" is answerable from the database rather than from logs that may be gone.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

GLOBAL_LIVE_ENV = "VANTAGE_LIVE_TRADING"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

# The DB flag is the operator-facing switch (ExecutionPanel). The env var is the
# hard brake: set it falsy and nothing can be live no matter what the UI says;
# set it truthy and live is forced on for that process (bootstrap/CI). Unset —
# the normal case — defers to the DB.
_CACHE: dict = {"at": 0.0, "value": False}
_CACHE_TTL = 2.0


def _db_live_flag() -> bool:
    """Read live_mode.enabled straight from SQLite (sync, cached ~2s).

    Deliberately sync: this predicate is called from request handlers, daemons
    and CLI paths, and must not require an event loop to answer "may I sign".
    """
    import time as _time

    now = _time.monotonic()
    if now - _CACHE["at"] < _CACHE_TTL:
        return bool(_CACHE["value"])
    value = False
    try:
        import sqlite3

        from .db import DB_PATH
    except ImportError:  # backend/ on sys.path
        try:
            import sqlite3  # noqa: F401

            from db import DB_PATH  # type: ignore
        except Exception:
            return False
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
        try:
            try:
                row = con.execute("SELECT enabled, COALESCE(set_in_demo, 0) FROM live_mode WHERE id=1").fetchone()
            except sqlite3.OperationalError:  # pre-migration: no set_in_demo column
                r = con.execute("SELECT enabled FROM live_mode WHERE id=1").fetchone()
                row = (r[0], 0) if r else None
        finally:
            con.close()
        value = bool(row and row[0]) and not (bool(row[1]) and not demo_mode())
    except Exception:
        # A missing table (pre-migration) or a locked DB must read as OFF.
        value = False
    _CACHE.update(at=now, value=value)
    return value


def invalidate_cache() -> None:
    _CACHE.update(at=0.0, value=False)


def env_live_value() -> Optional[str]:
    v = os.environ.get(GLOBAL_LIVE_ENV)
    return None if v is None else str(v).strip().lower()


def _settings():
    try:
        from .config import settings
    except ImportError:
        from config import settings  # type: ignore
    return settings


def demo_mode() -> bool:
    """The deployment is a demo: live path testable, real settlement refused."""
    return bool(getattr(_settings(), "DEMO_MODE", False))


def settlement_allowed() -> bool:
    """May anything settle on a real chain? Never in demo, whatever the switch."""
    return not demo_mode()


def env_ceiling_open() -> bool:
    """Layer 0. Live is possible on this process only when the operator has
    explicitly opened the ceiling. Unset is the brake: no install ships armed.
    In demo the ceiling reads open (testing is unhindered) because settlement
    is refused at the edge instead."""
    return demo_mode() or env_live_value() in _TRUTHY


def global_live_enabled() -> bool:
    """Can anything sign right now? Ceiling open AND operator switch on."""
    return env_ceiling_open() and _db_live_flag()


def live_mode_status() -> dict:
    """Everything needed to render the switch honestly."""
    env = env_live_value()
    db_on = _db_live_flag()
    ceiling = env in _TRUTHY
    return {
        "enabled": ceiling and db_on,
        "source": "db" if ceiling else "env_brake",
        "db_enabled": db_on,
        "env": os.environ.get(GLOBAL_LIVE_ENV),
        "env_brake_active": not ceiling,
        "env_ceiling_open": ceiling,
        "demo_mode": demo_mode(),
        "settlement_allowed": settlement_allowed(),
        # Kept for older clients: the env var can no longer force live on.
        "env_override_active": False,
    }


def paper_mode_always_on() -> bool:
    """Paper trading cannot be disabled. Exists so callers can assert it."""
    return True


def rs2_applies_to(mode: str) -> bool:
    """Where an RS-2-style approval gate belongs, if it is ever wired in.

    Paper never waits for a second approver. Live may — that is a policy choice
    for whoever wires it, but the seam is here so it cannot be applied to paper
    by accident.
    """
    return mode == "live"


def live_order_allowed(*, armed: bool, live: bool, wallet_bound: bool = False,
                       kill_engaged: bool = True,
                       global_enabled: Optional[bool] = None) -> tuple[bool, str]:
    """Pure predicate over every layer. Returns (allowed, reason-if-not).

    `wallet_bound` and `kill_engaged` default to the closed value, so a caller
    that does not know them gets paper, never live.
    """
    if global_enabled is None:
        global_enabled = global_live_enabled()
    if not armed:
        return False, "strategy_not_armed"
    if not live:
        return False, "strategy_live_gate_closed"
    if not global_enabled:
        return False, "global_live_disabled"
    if not wallet_bound:
        return False, "strategy_wallet_unbound"
    if kill_engaged:
        return False, "account_live_revoked"
    if not settlement_allowed():
        return False, "demo_mode_settlement_refused"
    return True, ""


def mode_for_strategy(armed, live, wallet_bound: bool = False, kill_engaged: bool = True) -> str:
    """"live" only when every per-strategy and account layer is on."""
    ok = bool(armed) and bool(live) and bool(wallet_bound) and not bool(kill_engaged)
    return "live" if ok else "paper"


# ── Layer 2: the account kill switch ────────────────────────────────────────

def kill_switch_engaged(agent_id: Optional[int]) -> bool:
    """Sync read of the account kill switch. Unknown agent, missing table or a
    locked DB all read as ENGAGED: when in doubt, paper."""
    if agent_id is None:
        return True
    try:
        import sqlite3

        try:
            from .db import DB_PATH
        except ImportError:
            from db import DB_PATH  # type: ignore
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
        try:
            row = con.execute("SELECT engaged FROM live_killswitch WHERE agent_id=?", (int(agent_id),)).fetchone()
        finally:
            con.close()
    except Exception:
        return True
    # No row = the account never opted out. Layer 1 (default off) still applies.
    return bool(row and row[0])


def denylisted(stem: str) -> bool:
    """Instance deny-list for *_trading_enabled stems (instance config)."""
    try:
        from .config import settings
    except ImportError:
        from config import settings  # type: ignore
    deny = {x.strip().lower() for x in (settings.TRADING_DENYLIST or "").split(",") if x.strip()}
    return stem.lower() in deny


async def record_mode_change(db, *, agent_id: Optional[int], scope: str, action: str,
                             strategy_id: Optional[int] = None,
                             prev_state: Optional[str] = None,
                             new_state: Optional[str] = None,
                             note: str = "", actor: str = "") -> None:
    """Append one audit row. Never raises: an audit failure must not block a
    disarm, but it is logged loudly because a silent audit gap is worse."""
    try:
        await db.execute(
            """INSERT INTO trading_mode_audit
               (agent_id, scope, strategy_id, action, prev_state, new_state, note, actor)
               VALUES (?,?,?,?,?,?,?,?)""",
            (agent_id, scope, strategy_id, action, prev_state, new_state,
             (note or "")[:500], (actor or "")[:200]),
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - audit must not break the toggle
        logger.error("trading_mode: audit write failed (%s): %s", action, exc)


async def recent_mode_changes(db, limit: int = 50) -> list[dict]:
    """Most recent audit rows, newest first — for the audit surface and tests."""
    import aiosqlite
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        """SELECT id, ts, agent_id, scope, strategy_id, action, prev_state,
                  new_state, note, actor
           FROM trading_mode_audit ORDER BY id DESC LIMIT ?""",
        (int(limit),),
    )
    return [dict(r) for r in await cur.fetchall()]


async def record_boot_mode(actor: str = "startup") -> None:
    """Write one row recording the mode this process booted in.

    The per-strategy gates are audited when they change; the global switch is an
    environment variable, so without a boot row "was live even possible during
    that window" cannot be answered after a restart. Never raises.
    """
    try:
        from .db import get_db
    except ImportError:  # backend/ on sys.path (scripts, tests)
        from db import get_db  # type: ignore

    enabled = global_live_enabled()
    try:
        async with get_db() as db:
            await record_mode_change(
                db, agent_id=None, scope="global", action="process_start",
                prev_state=None,
                new_state="live_enabled" if enabled else "paper_only",
                note=f"{GLOBAL_LIVE_ENV}={os.environ.get(GLOBAL_LIVE_ENV, '<unset>')}",
                actor=actor,
            )
        if not enabled:
            logger.info("Live trading off (%s ceiling %s) — paper only.", GLOBAL_LIVE_ENV,
                        "open" if env_ceiling_open() else "closed")
    except Exception as exc:  # never block startup on an audit row
        logger.warning("could not record startup trading mode: %s", exc)
