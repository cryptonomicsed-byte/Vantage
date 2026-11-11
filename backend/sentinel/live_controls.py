"""The only code that changes the instance live switch or a signer's arm.

Two directions, two rules:
  * Toward paper (switch off, disarm): applied immediately by any operator.
    Never routed through Èṣù or a sign-off — a brake must not wait on anything.
  * Toward live (switch on, arm, pick a signer wallet): only from an approved,
    Èṣù-cleared sign-off (see signoffs.py). Preconditions are re-checked at
    apply time, because the world may have changed since the card was filed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from .. import trading_mode
from ..db import get_db

TRADING_ARM_SUFFIX = "_trading_enabled"


def _stem(key: str) -> str:
    return key[: -len(TRADING_ARM_SUFFIX)] if key.endswith(TRADING_ARM_SUFFIX) else key


async def set_live_mode(*, enabled: bool, actor: str, note: str = "", agent_id: Optional[int] = None) -> dict:
    if enabled and not trading_mode.env_ceiling_open():
        raise HTTPException(
            409,
            f"Cannot enable live trading: the instance ceiling {trading_mode.GLOBAL_LIVE_ENV} is closed. "
            "Live stays paper until an operator opens it in the service environment.",
        )
    async with get_db() as db:
        row = await (await db.execute("SELECT enabled FROM live_mode WHERE id=1")).fetchone()
        prev_state = "live" if (row and row[0]) else "paper"
        await db.execute(
            "UPDATE live_mode SET enabled=?, changed_by=?, changed_at=datetime('now'), note=?, set_in_demo=? WHERE id=1",
            (1 if enabled else 0, agent_id, (note or "")[:500], 1 if (enabled and trading_mode.demo_mode()) else 0),
        )
        await trading_mode.record_mode_change(
            db, agent_id=agent_id, scope="global",
            action="live_mode_on" if enabled else "live_mode_off",
            prev_state=prev_state, new_state="live" if enabled else "paper",
            note=note or "", actor=actor,
        )
        await db.commit()
    trading_mode.invalidate_cache()
    return trading_mode.live_mode_status()


async def set_daemon_setting(*, key: str, value: str, actor: str, note: str = "") -> dict:
    arming = key.endswith(TRADING_ARM_SUFFIX) and value == "1"
    if arming:
        if trading_mode.denylisted(_stem(key)):
            raise HTTPException(403, f"'{key}' is on this instance's trading deny-list and cannot be armed")
        if not trading_mode.global_live_enabled():
            raise HTTPException(
                409,
                f"Cannot arm '{key}': live is off for this instance (paper only). Disarming is always allowed.",
            )
    async with get_db() as db:
        if not key.endswith(TRADING_ARM_SUFFIX):
            wallet = await (await db.execute("SELECT id FROM trading_wallets WHERE id=?", (value,))).fetchone()
            if not wallet:
                raise HTTPException(422, "value must be an existing wallet_id")
        await db.execute(
            "INSERT INTO daemon_settings (key, value, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value),
        )
        action = ("daemon_arm" if value == "1" else "daemon_disarm") if key.endswith(TRADING_ARM_SUFFIX) \
            else "daemon_wallet"
        await trading_mode.record_mode_change(
            db, agent_id=None, scope="daemon", action=action, prev_state=None,
            new_state=("armed" if value == "1" else "disarmed") if key.endswith(TRADING_ARM_SUFFIX) else value,
            note=f"{key} {note}".strip(), actor=actor,
        )
        await db.commit()
    return {"key": key, "value": value}
