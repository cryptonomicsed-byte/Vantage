"""Master switch for anything that can spend money.

Auto-execution and swarm scans are *off* unless BOTH of these hold:

    env  VANTAGE_AUTOTRADE=1                    (deployment intent)
    db   app_flags['autotrade'].value == 'on'   (operator intent)

Two keys on purpose: a stray environment variable cannot arm trading, and a
stray API call cannot arm it either. Either one being off means no order, no
LLM spend, no swarm scan.

Every read fails closed. If the DB is unreachable the answer is OFF, never ON --
an unreachable control plane must not look like permission.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("VANTAGE_DB", "/opt/ares/Vantage/data/vantage.db")

_ON = {"on", "1", "true", "yes", "enabled"}


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=3.0)


def ensure_schema() -> None:
    try:
        with _connect() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS app_flags (
                       key        TEXT PRIMARY KEY,
                       value      TEXT NOT NULL,
                       updated_at TEXT NOT NULL,
                       updated_by TEXT
                   )"""
            )
    except Exception as e:  # pragma: no cover - defensive
        log.warning("app_flags schema unavailable: %s", e)


def _db_on() -> bool:
    try:
        with _connect() as c:
            row = c.execute("SELECT value FROM app_flags WHERE key='autotrade'").fetchone()
        return bool(row) and str(row[0]).strip().lower() in _ON
    except Exception:
        return False  # fail closed


def env_on() -> bool:
    return os.environ.get("VANTAGE_AUTOTRADE", "0").strip() == "1"


def autotrade_enabled() -> bool:
    """True only when deployment AND operator both say yes."""
    return env_on() and _db_on()


def set_flag(on: bool, by: str = "api") -> dict:
    ensure_schema()
    value = "on" if on else "off"
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as c:
        c.execute(
            """INSERT INTO app_flags(key, value, updated_at, updated_by)
               VALUES('autotrade', ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,
                   updated_at=excluded.updated_at,
                   updated_by=excluded.updated_by""",
            (value, now, by),
        )
    return status()


def status() -> dict:
    return {
        "enabled": autotrade_enabled(),
        "env_flag": env_on(),
        "runtime_flag": _db_on(),
        "detail": (
            "armed" if autotrade_enabled()
            else "env off" if not env_on()
            else "operator toggle off"
        ),
    }
