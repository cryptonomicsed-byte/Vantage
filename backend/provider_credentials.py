"""CRUD for per-agent LLM/AI provider API keys (Settings > Mind & LLM).
Real encryption at rest (secret_vault.py, AES-256-GCM, server-side master
key), masked-only display, never logs a raw key. See db.py's
provider_credentials table comment for the schema rationale.
"""
import logging
from typing import Optional

import aiosqlite

from .db import get_db
from .provider_registry import get_provider, is_custom_provider_id
from .secret_vault import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


def _principal(agent_id: int, provider_id: str) -> str:
    return f"provider-key:{agent_id}:{provider_id}"


async def save_credential(
    agent_id: int,
    provider_id: str,
    api_key: str,
    *,
    display_name: Optional[str] = None,
    base_url: Optional[str] = None,
    model_default: Optional[str] = None,
) -> dict:
    """Insert or replace this agent's key for `provider_id`. Never logs
    `api_key` -- only its masked form ever reaches a log line, and only at
    debug-adjacent call sites that already avoid this, not here at all."""
    if not api_key or not api_key.strip():
        raise ValueError("api_key is required")
    api_key = api_key.strip()

    is_custom = is_custom_provider_id(provider_id)
    if is_custom:
        if not display_name or not display_name.strip():
            raise ValueError("display_name is required for a custom provider")
        if not base_url or not base_url.strip():
            raise ValueError("base_url is required for a custom provider")
    else:
        info = get_provider(provider_id)
        if info is None:
            raise ValueError(f"unknown provider_id {provider_id!r}")
        display_name = display_name or info.display_name
        base_url = base_url or info.base_url_default
        model_default = model_default or info.model_default

    encrypted = encrypt_secret(_principal(agent_id, provider_id), api_key)
    # Store ONLY the raw last-4 characters -- not reversible to the real key
    # on its own, and lets list_credentials build a consistent display mask
    # without needing to know the original key's length.
    last4 = api_key[-4:] if len(api_key) >= 4 else api_key

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO provider_credentials
                (agent_id, provider_id, display_name, base_url, model_default,
                 key_encrypted, key_last4, is_custom, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(agent_id, provider_id) DO UPDATE SET
                display_name=excluded.display_name,
                base_url=excluded.base_url,
                model_default=excluded.model_default,
                key_encrypted=excluded.key_encrypted,
                key_last4=excluded.key_last4,
                updated_at=datetime('now')
            """,
            (agent_id, provider_id, display_name, base_url, model_default,
             encrypted, last4, int(is_custom)),
        )
        await db.commit()

    logger.info(
        "provider credential saved: agent_id=%s provider_id=%s key=%s",
        agent_id, provider_id, last4,
    )
    return {"ok": True, "provider_id": provider_id, "masked_key": "••••••••" + last4}


async def list_credentials(agent_id: int) -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT provider_id, display_name, base_url, model_default, key_last4, "
            "is_custom, is_active, created_at, updated_at FROM provider_credentials "
            "WHERE agent_id = ? ORDER BY provider_id",
            (agent_id,),
        ) as cur:
            rows = await cur.fetchall()

    out = []
    for r in rows:
        row = dict(r)
        info = None if row["is_custom"] else get_provider(row["provider_id"])
        out.append({
            "provider_id": row["provider_id"],
            "display_name": row["display_name"],
            "base_url": row["base_url"],
            "model_default": row["model_default"],
            "masked_key": "••••••••" + row["key_last4"],
            "is_custom": bool(row["is_custom"]),
            "is_active": bool(row["is_active"]),
            "chat_compatible": bool(info.chat_compatible) if info else True,  # custom providers assert it themselves
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return out


async def delete_credential(agent_id: int, provider_id: str) -> dict:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM provider_credentials WHERE agent_id = ? AND provider_id = ?",
            (agent_id, provider_id),
        )
        await db.commit()
        deleted = cur.rowcount > 0
    return {"ok": True, "deleted": deleted}


async def set_active_provider(agent_id: int, provider_id: Optional[str]) -> dict:
    """Selects which stored credential Copilot's chat fallback should use
    (llm_provider_client.py). `provider_id=None` clears the selection,
    falling back to OmniRoute -- requirement: UI keys take precedence when
    set, .env/OmniRoute stays the fallback when not."""
    async with get_db() as db:
        await db.execute("UPDATE provider_credentials SET is_active = 0 WHERE agent_id = ?", (agent_id,))
        if provider_id:
            cur = await db.execute(
                "UPDATE provider_credentials SET is_active = 1 WHERE agent_id = ? AND provider_id = ?",
                (agent_id, provider_id),
            )
            if cur.rowcount == 0:
                await db.rollback()
                raise ValueError(f"no stored credential for provider_id {provider_id!r}")
        await db.commit()
    return {"ok": True, "active_provider_id": provider_id}


async def get_active_provider_for_chat(agent_id: int) -> Optional[dict]:
    """Internal use only (llm_provider_client.py) -- returns the DECRYPTED
    key. Never expose this dict, or anything derived from it, through an
    API response; never log `api_key`."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT provider_id, base_url, model_default, key_encrypted FROM provider_credentials "
            "WHERE agent_id = ? AND is_active = 1 LIMIT 1",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None

    provider_id = row["provider_id"]
    is_custom = is_custom_provider_id(provider_id)
    info = None if is_custom else get_provider(provider_id)
    if info is not None and not info.chat_compatible:
        # Selected provider exists but isn't wired for chat calls -- don't
        # silently attempt a request shape that will just error. Caller
        # falls back to OmniRoute exactly as if nothing were selected.
        return None

    api_key = decrypt_secret(_principal(agent_id, provider_id), row["key_encrypted"])
    return {
        "provider_id": provider_id,
        "base_url": row["base_url"] or (info.base_url_default if info else None),
        "model": row["model_default"] or (info.model_default if info else None),
        "auth_header_style": info.auth_header_style if info else "bearer",
        "api_key": api_key,
    }
