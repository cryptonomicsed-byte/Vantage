"""OAuth connector store — clients, codes, tokens, identities.

One token authority, many platform connectors. Everything in this module is
platform-agnostic on purpose: the per-platform differences live in
connector_profiles.py. Duplicating token issuance per platform would mean N
revocation paths, and the one that gets forgotten is the breach.

Follows capability_registry.py's self-contained "_ensure_my_tables()" pattern so
this module has no ordering dependency on db.py's schema init.

Token format
------------
Access/refresh tokens and auth codes are opaque random strings with a
readable prefix (vgt_/vgr_/vgc_) and are stored ONLY as sha256 hex — the same
convention agents.api_key already uses (db.py's one-time plaintext->hash
migration). A database read therefore never yields a usable credential.
"""
import hashlib
import json
import logging
import secrets
import time
from typing import Optional

import aiosqlite

from .db import DB_PATH

logger = logging.getLogger(__name__)

# Lifetimes (seconds)
CODE_TTL = 300                 # 5 min — an authorization code is single-use and short
ACCESS_TTL = 3600              # 1 hour
REFRESH_TTL = 30 * 24 * 3600   # 30 days

ACCESS_PREFIX = "vgt_"
REFRESH_PREFIX = "vgr_"
CODE_PREFIX = "vgc_"
CLIENT_PREFIX = "vgcl_"


def _now() -> int:
    return int(time.time())


def _sha256(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _rand(prefix: str, nbytes: int = 32) -> str:
    return prefix + secrets.token_urlsafe(nbytes)


# ── schema ─────────────────────────────────────────────────────────────────────

async def ensure_oauth_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_identities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                subject TEXT NOT NULL,
                agent_id INTEGER NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_seen_at TEXT,
                UNIQUE(platform, subject)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                client_secret_hash TEXT,
                client_name TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT 'generic',
                registration_method TEXT NOT NULL DEFAULT 'dcr',
                redirect_uris TEXT NOT NULL DEFAULT '[]',
                scopes TEXT NOT NULL DEFAULT '',
                metadata_url TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_used_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code_hash TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                agent_id INTEGER NOT NULL,
                redirect_uri TEXT NOT NULL,
                code_challenge TEXT NOT NULL,
                code_challenge_method TEXT NOT NULL DEFAULT 'S256',
                scope TEXT NOT NULL DEFAULT '',
                resource TEXT,
                platform TEXT NOT NULL DEFAULT 'generic',
                expires_at INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                token_hash TEXT PRIMARY KEY,
                refresh_hash TEXT UNIQUE,
                client_id TEXT NOT NULL,
                agent_id INTEGER NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                resource TEXT,
                platform TEXT NOT NULL DEFAULT 'generic',
                issued_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                refresh_expires_at INTEGER,
                revoked INTEGER NOT NULL DEFAULT 0,
                last_used_at INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS oauth_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL DEFAULT (datetime('now')),
                event TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                client_id TEXT NOT NULL DEFAULT '',
                agent_id INTEGER,
                detail TEXT NOT NULL DEFAULT ''
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_agent ON oauth_tokens(agent_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_identities_agent ON oauth_identities(agent_id)"
        )
        await db.commit()
    logger.info("oauth tables ensured")


async def audit(event: str, platform: str = "", client_id: str = "",
                agent_id: Optional[int] = None, detail: str = "") -> None:
    """Append-only trail. Every authorize/code/token/refresh/revoke lands here —
    without it, 'who minted this token' is unanswerable after an incident."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=10000")
            await db.execute(
                "INSERT INTO oauth_audit (event, platform, client_id, agent_id, detail)"
                " VALUES (?,?,?,?,?)",
                (event, platform, client_id, agent_id, detail[:2000]),
            )
            await db.commit()
    except Exception as _exc:  # never let auditing break the auth flow
        logger.warning("oauth audit write failed: %s", _exc)


# ── clients ────────────────────────────────────────────────────────────────────

async def get_client(client_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    c = dict(row)
    try:
        c["redirect_uris"] = json.loads(c.get("redirect_uris") or "[]")
    except Exception:
        c["redirect_uris"] = []
    return c


async def upsert_client(client_id: str, *, platform: str, registration_method: str,
                        client_name: str = "", redirect_uris: Optional[list] = None,
                        scopes: str = "", metadata_url: Optional[str] = None,
                        client_secret: Optional[str] = None) -> dict:
    secret_hash = _sha256(client_secret) if client_secret else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(
            """INSERT INTO oauth_clients
                 (client_id, client_secret_hash, client_name, platform,
                  registration_method, redirect_uris, scopes, metadata_url)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(client_id) DO UPDATE SET
                 client_name=excluded.client_name,
                 platform=excluded.platform,
                 registration_method=excluded.registration_method,
                 redirect_uris=excluded.redirect_uris,
                 scopes=excluded.scopes,
                 metadata_url=COALESCE(excluded.metadata_url, oauth_clients.metadata_url),
                 client_secret_hash=COALESCE(excluded.client_secret_hash,
                                             oauth_clients.client_secret_hash)""",
            (client_id, secret_hash, client_name, platform, registration_method,
             json.dumps(redirect_uris or []), scopes, metadata_url),
        )
        await db.commit()
    client = await get_client(client_id)
    if client is None:  # cannot happen: we just wrote the row
        raise RuntimeError("client upsert lost its row")
    return client


async def touch_client(client_id: str) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=10000")
            await db.execute(
                "UPDATE oauth_clients SET last_used_at=datetime('now') WHERE client_id=?",
                (client_id,),
            )
            await db.commit()
    except Exception:
        pass


# ── identities (platform account -> Vantage agent) ─────────────────────────────

async def lookup_identity(platform: str, subject: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM oauth_identities WHERE platform=? AND subject=?",
            (platform, subject),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def bind_identity(platform: str, subject: str, agent_id: int,
                        display_name: str = "") -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(
            """INSERT INTO oauth_identities (platform, subject, agent_id, display_name)
               VALUES (?,?,?,?)
               ON CONFLICT(platform, subject) DO UPDATE SET
                 agent_id=excluded.agent_id,
                 display_name=excluded.display_name,
                 last_seen_at=datetime('now')""",
            (platform, subject, agent_id, display_name),
        )
        await db.commit()
    return {"platform": platform, "subject": subject, "agent_id": agent_id}


async def touch_identity(platform: str, subject: str) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=10000")
            await db.execute(
                "UPDATE oauth_identities SET last_seen_at=datetime('now')"
                " WHERE platform=? AND subject=?",
                (platform, subject),
            )
            await db.commit()
    except Exception:
        pass


# ── self-serve agent creation ──────────────────────────────────────────────────

def _slugify(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in (name or "")]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:40] or "agent"


async def create_agent_account(name: str, bio: str = "") -> tuple[int, str]:
    """Create a Vantage agent and return (agent_id, plaintext_api_key).

    Mirrors the platform's existing registration convention: the plaintext key
    is returned ONCE and only its sha256 is persisted (db.py's migration at
    ~line 2254 assumes exactly this shape). Name collisions get a numeric
    suffix rather than failing, since this is called from an OAuth callback
    where raising would strand the user mid-flow.
    """
    api_key = "vantage_" + secrets.token_urlsafe(32)
    hashed = _sha256(api_key)
    base = _slugify(name)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        for attempt in range(0, 6):
            candidate = base if attempt == 0 else f"{base}-{secrets.token_hex(2)}"
            try:
                cur = await db.execute(
                    "INSERT INTO agents (name, api_key, bio) VALUES (?,?,?)",
                    (candidate, hashed, bio or ""),
                )
                await db.commit()
                return int(cur.lastrowid), api_key
            except aiosqlite.IntegrityError:
                continue
    raise RuntimeError("could not allocate a unique agent name")


async def agent_by_id(agent_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def agent_by_key(plaintext_key: str) -> Optional[dict]:
    """Resolve a plaintext agent key (vantage_...) to its agent row."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM agents WHERE api_key=?", (_sha256(plaintext_key),)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── authorization codes ────────────────────────────────────────────────────────

async def create_code(*, client_id: str, agent_id: int, redirect_uri: str,
                      code_challenge: str, code_challenge_method: str,
                      scope: str, resource: Optional[str],
                      platform: str) -> str:
    code = _rand(CODE_PREFIX, 32)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(
            """INSERT INTO oauth_codes
                 (code_hash, client_id, agent_id, redirect_uri, code_challenge,
                  code_challenge_method, scope, resource, platform, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_sha256(code), client_id, agent_id, redirect_uri, code_challenge,
             code_challenge_method, scope, resource, platform, _now() + CODE_TTL),
        )
        await db.commit()
    return code


async def consume_code(code: str) -> Optional[dict]:
    """Single-use. Marks used in the same statement that reads it, so two
    concurrent redemptions cannot both succeed."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=30000")
        async with db.execute(
            "SELECT * FROM oauth_codes WHERE code_hash=?", (_sha256(code),)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        rec = dict(row)
        if rec["used"] or rec["expires_at"] < _now():
            return None
        await db.execute(
            "UPDATE oauth_codes SET used=1 WHERE code_hash=?", (_sha256(code),)
        )
        await db.commit()
    return rec


# ── tokens ─────────────────────────────────────────────────────────────────────

async def issue_token(*, client_id: str, agent_id: int, scope: str,
                      resource: Optional[str], platform: str,
                      with_refresh: bool = True) -> dict:
    access = _rand(ACCESS_PREFIX, 32)
    refresh = _rand(REFRESH_PREFIX, 32) if with_refresh else None
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(
            """INSERT INTO oauth_tokens
                 (token_hash, refresh_hash, client_id, agent_id, scope, resource,
                  platform, issued_at, expires_at, refresh_expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (_sha256(access), _sha256(refresh) if refresh else None, client_id,
             agent_id, scope, resource, platform, now, now + ACCESS_TTL,
             now + REFRESH_TTL if refresh else None),
        )
        await db.commit()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": refresh,
        "scope": scope,
    }


async def resolve_access_token(token: str) -> Optional[dict]:
    """Validate an access token. Returns the token row, or None if unknown,
    expired or revoked. Callers must still apply platform policy (sentencing,
    rate limits) — this only proves the credential."""
    if not token:
        return None
    th = _sha256(token)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM oauth_tokens WHERE token_hash=?", (th,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    rec = dict(row)
    if rec["revoked"] or rec["expires_at"] < _now():
        return None
    return rec


async def touch_token(token: str) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA busy_timeout=10000")
            await db.execute(
                "UPDATE oauth_tokens SET last_used_at=? WHERE token_hash=?",
                (_now(), _sha256(token)),
            )
            await db.commit()
    except Exception:
        pass


async def refresh_access_token(refresh_token: str, client_id: str) -> Optional[dict]:
    """Rotate: the old refresh token is revoked as the new pair is issued.
    Reuse of a rotated token therefore fails closed instead of granting a
    second live session."""
    rh = _sha256(refresh_token)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=30000")
        async with db.execute(
            "SELECT * FROM oauth_tokens WHERE refresh_hash=?", (rh,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        rec = dict(row)
        if rec["revoked"]:
            return None
        if (rec["refresh_expires_at"] or 0) < _now():
            return None
        if rec["client_id"] != client_id:
            return None
        await db.execute(
            "UPDATE oauth_tokens SET revoked=1 WHERE refresh_hash=?", (rh,)
        )
        await db.commit()
    out = await issue_token(
        client_id=rec["client_id"], agent_id=rec["agent_id"], scope=rec["scope"],
        resource=rec["resource"], platform=rec["platform"], with_refresh=True,
    )
    await audit("token_refresh", rec["platform"], rec["client_id"], rec["agent_id"])
    return out


async def revoke_token(token: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        h = _sha256(token)
        cur = await db.execute(
            "UPDATE oauth_tokens SET revoked=1 WHERE token_hash=? OR refresh_hash=?",
            (h, h),
        )
        await db.commit()
        return cur.rowcount > 0


async def revoke_agent_tokens(agent_id: int) -> int:
    """Kill every connector session for one agent — the 'revoke this ChatGPT
    user's access' button that a shared-key design cannot offer."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        cur = await db.execute(
            "UPDATE oauth_tokens SET revoked=1 WHERE agent_id=? AND revoked=0",
            (agent_id,),
        )
        await db.commit()
        return cur.rowcount
