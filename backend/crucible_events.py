"""Crucible plumbing shared by the trading adapter (P3-01) and the daily
calibration events (P3-03): signing keys, NIP-01 signing, the crucible CLI,
the event outbox and the optional relay push.

Everything here fails open. Crucible is not installed on the box yet, keys
may be missing, the relay may be down: every one of those is a logged skip,
never an exception reaching a caller, and never a change to signal ingest.

Keys
----
Per-source signing keys live on the VPS only and are generated there at
deploy time (``crucible keygen.live``, P1-17). Config points at files; this
module never generates, prints or logs a secret, and never hands one to the
CLI -- events are signed here, in-process, after the CLI builds them.

  CRUCIBLE_SOURCE_KEYS_DIR   dir of ``<source>.key`` files   (/etc/ares/crucible/sources)
  CRUCIBLE_ROUTER_KEY_FILE   key the guild router signs with (/etc/ares/crucible/guild-router.key)
  CRUCIBLE_RESOLVER_KEY_FILE the oracle/resolver key         (/etc/ares/crucible/resolver.json)

A key file holds the 32-byte secret as raw bytes, as 64 hex chars, or as the
JSON ``{"secret_key": "<hex>", ...}`` that ``keygen.live`` prints. A file that
is group- or world-readable is refused (treated as missing).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

KIND_CLAIM = 47001
KIND_ATTESTATION = 47002
KIND_CALIBRATION = 47005
KIND_ORACLE_VERDICT = 47010

DEFAULT_KEYS_DIR = "/etc/ares/crucible/sources"
DEFAULT_ROUTER_KEY = "/etc/ares/crucible/guild-router.key"
DEFAULT_RESOLVER_KEY = "/etc/ares/crucible/resolver.json"
CLI_TIMEOUT_S = 20.0
_SOURCE_FILE = re.compile(r"[^A-Za-z0-9._-]")


def flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


def community() -> str:
    return os.environ.get("CRUCIBLE_COMMUNITY", "vantage-trading").strip() or "vantage-trading"


# ── keys ─────────────────────────────────────────────────────────────────────

def _parse_secret(raw: bytes) -> Optional[bytes]:
    if len(raw) == 32:
        return raw
    text = raw.decode("utf-8", "ignore").strip()
    if text.startswith("{"):
        try:
            text = str(json.loads(text).get("secret_key") or "")
        except (ValueError, AttributeError):
            return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return bytes.fromhex(text)
    return None


def load_secret(path: str | os.PathLike | None) -> Optional[bytes]:
    """The 32-byte secret at ``path``, or None (missing, unreadable, malformed,
    or readable by group/other). Never raises, never logs key material."""
    if not path:
        return None
    p = Path(path)
    try:
        st = p.stat()
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_mode & 0o077:
            logger.warning("crucible key %s is group/world-accessible; refusing it", p)
            return None
        return _parse_secret(p.read_bytes())
    except OSError:
        return None


def source_key_path(source: str) -> Path:
    base = os.environ.get("CRUCIBLE_SOURCE_KEYS_DIR", DEFAULT_KEYS_DIR)
    return Path(base) / f"{_SOURCE_FILE.sub('_', source)[:64]}.key"


def source_key(source: str) -> Optional[bytes]:
    return load_secret(source_key_path(source))


def router_key() -> Optional[bytes]:
    return load_secret(os.environ.get("CRUCIBLE_ROUTER_KEY_FILE", DEFAULT_ROUTER_KEY))


def resolver_key() -> Optional[bytes]:
    return load_secret(os.environ.get("CRUCIBLE_RESOLVER_KEY_FILE", DEFAULT_RESOLVER_KEY))


def pubkey_hex(secret: bytes) -> str:
    """BIP-340 x-only public key, hex."""
    from coincurve import PrivateKey
    return PrivateKey(secret).public_key.format(compressed=True)[1:].hex()


# ── NIP-01 signing (wire-identical to crucible-core's canonical_bytes) ───────

def canonical(pubkey: str, created_at: int, kind: int, tags: list, content: str) -> bytes:
    # serde_json's escape set == json.dumps with ensure_ascii=False and no
    # whitespace; both leave '/' and non-ASCII alone.
    return json.dumps([0, pubkey, int(created_at), int(kind), tags, content],
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def event_id(ev: dict) -> str:
    return hashlib.sha256(canonical(ev["pubkey"], ev["created_at"], ev["kind"],
                                    ev["tags"], ev["content"])).hexdigest()


def sign(unsigned: dict, secret: bytes) -> dict:
    """Sign an event. Refuses (ValueError) if the event's pubkey is not this
    key's -- signing someone else's event would only produce a bad sig."""
    from coincurve import PrivateKey
    pk = pubkey_hex(secret)
    if unsigned.get("pubkey") and unsigned["pubkey"] != pk:
        raise ValueError("event pubkey does not match the signing key")
    ev = {"pubkey": pk, "created_at": int(unsigned["created_at"]), "kind": int(unsigned["kind"]),
          "tags": [list(map(str, t)) for t in unsigned.get("tags") or []],
          "content": str(unsigned.get("content") or "")}
    ev["id"] = event_id(ev)
    ev["sig"] = PrivateKey(secret).sign_schnorr(bytes.fromhex(ev["id"]), secrets.token_bytes(32)).hex()
    return ev


def verify(ev: dict) -> bool:
    try:
        from coincurve import PublicKeyXOnly
        if event_id(ev) != ev.get("id"):
            return False
        return PublicKeyXOnly(bytes.fromhex(ev["pubkey"])).verify(
            bytes.fromhex(ev["sig"]), bytes.fromhex(ev["id"]))
    except Exception:
        return False


def add_tags(unsigned: dict, extra: list[list[str]]) -> dict:
    """Append tags. Crucible reads single-valued tags first-wins, so appended
    tags can never override one the CLI set."""
    return {**unsigned, "tags": list(unsigned.get("tags") or []) + [list(map(str, t)) for t in extra]}


# ── the crucible CLI (one JSON in, one JSON out) ─────────────────────────────

def cli_path() -> Optional[str]:
    explicit = os.environ.get("CRUCIBLE_BIN", "").strip()
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    return shutil.which("crucible")


def cli_available() -> bool:
    return cli_path() is not None


def cli(verb: str, payload: dict) -> Optional[dict]:
    """Run one verb. None if the CLI is missing or the verb failed (the error
    is logged, not raised). Secrets are never part of a payload sent here."""
    binp = cli_path()
    if not binp:
        return None
    env = dict(os.environ)
    fdir = os.environ.get("CRUCIBLE_FALSIFIER_DIR")
    if fdir:
        env["CRUCIBLE_FALSIFIER_DIR"] = fdir
    try:
        r = subprocess.run([binp, verb], input=json.dumps(payload).encode(), capture_output=True,
                           timeout=CLI_TIMEOUT_S, env=env, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("crucible %s failed to run: %s", verb, e)
        return None
    if r.returncode != 0:
        logger.warning("crucible %s refused: %s", verb, r.stderr.decode("utf-8", "replace")[:500])
        return None
    try:
        out = json.loads(r.stdout or b"{}")
    except ValueError:
        logger.warning("crucible %s returned non-JSON", verb)
        return None
    return out if isinstance(out, dict) else None


# ── outbox ───────────────────────────────────────────────────────────────────

OUTBOX_DDL = (
    """CREATE TABLE IF NOT EXISTS crucible_events (
  event_id TEXT PRIMARY KEY,
  kind INTEGER NOT NULL,
  claim_id TEXT, record_id TEXT, source TEXT,
  pubkey TEXT NOT NULL,
  attested INTEGER NOT NULL DEFAULT 1,
  event TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  stored_at INTEGER NOT NULL,
  published_at INTEGER, publish_error TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_crucible_events_kind ON crucible_events(kind, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_crucible_events_claim ON crucible_events(claim_id)",
    "CREATE INDEX IF NOT EXISTS idx_crucible_events_unpub ON crucible_events(published_at)",
)


async def ensure_outbox(db) -> None:
    for ddl in OUTBOX_DDL:
        await db.execute(ddl)


async def store(db, ev: dict, *, claim_id: str = "", record_id: str = "", source: str = "",
                attested: bool = True) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO crucible_events (event_id, kind, claim_id, record_id, source, pubkey, "
        "attested, event, created_at, stored_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ev["id"], ev["kind"], claim_id or None, record_id or None, source or None, ev["pubkey"],
         1 if attested else 0, json.dumps(ev, ensure_ascii=False), ev["created_at"], int(time.time())))


async def _relay_send(url: str, events: list[dict], timeout: float = 10.0) -> dict[str, Optional[str]]:
    """event id -> None (accepted) or the relay's reason. Raises on connect."""
    import websockets
    out: dict[str, Optional[str]] = {}
    async with websockets.connect(url, open_timeout=timeout, close_timeout=2) as ws:
        for ev in events:
            await ws.send(json.dumps(["EVENT", ev], ensure_ascii=False))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if isinstance(msg, list) and msg[:1] == ["OK"] and len(msg) >= 3 and msg[1] == ev["id"]:
                    ok = bool(msg[2])
                    # a duplicate is a success: the relay already has it
                    reason = str(msg[3]) if len(msg) > 3 else ""
                    out[ev["id"]] = None if ok or reason.startswith("duplicate") else (reason or "rejected")
                    break
            else:
                out[ev["id"]] = "timeout"
    return out


async def publish_pending(limit: int = 200) -> dict:
    """Push unpublished events to CRUCIBLE_RELAY_URL. Unset -> events stay in
    the outbox (the signed record is still kept). Never raises."""
    url = os.environ.get("CRUCIBLE_RELAY_URL", "").strip()
    if not url:
        return {"published": 0, "failed": 0, "relay": None}
    from backend.db import get_db
    try:
        async with get_db() as db:
            await ensure_outbox(db)
            rows = await (await db.execute(
                "SELECT event FROM crucible_events WHERE published_at IS NULL "
                "ORDER BY created_at, stored_at LIMIT ?", (int(limit),))).fetchall()
        events = [json.loads(r[0]) for r in rows]
        if not events:
            return {"published": 0, "failed": 0, "relay": url}
        try:
            res = await _relay_send(url, events)
        except Exception as e:
            logger.warning("crucible relay %s unreachable (events kept): %s", url, e)
            return {"published": 0, "failed": 0, "relay": url, "error": "unreachable"}
        now = int(time.time())
        async with get_db() as db:
            for eid, err in res.items():
                if err is None:
                    await db.execute("UPDATE crucible_events SET published_at=?, publish_error=NULL "
                                     "WHERE event_id=?", (now, eid))
                else:
                    await db.execute("UPDATE crucible_events SET publish_error=? WHERE event_id=?",
                                     (err[:200], eid))
            await db.commit()
        return {"published": sum(1 for e in res.values() if e is None),
                "failed": sum(1 for e in res.values() if e is not None), "relay": url}
    except Exception as e:
        logger.warning("crucible publish failed (fail-open): %s", e)
        return {"published": 0, "failed": 0, "relay": url, "error": str(e)[:200]}
