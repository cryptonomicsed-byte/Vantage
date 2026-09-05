"""Freenet Git Bridge — Phase F6.

Pushes git bundles as Freenet contract state so repositories replicate
across peers without central infrastructure.

Contract key: HKDF-SHA256(salt=b'freenet-git-v1', ikm=guild_slug+'/'+repo_name)
State format: JSON { head_commit, bundle_b64, bundle_hash, repo_name, guild_slug, pushed_at, push_count }
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _derive_git_contract_key(guild_slug: str, repo_name: str) -> str:
    """Derive a deterministic contract key for a guild/repo pair.

    Uses HKDF-SHA256 with domain separation b"freenet-git-v1" and
    the combined ikm = guild_slug + '/' + repo_name.
    Returns a 64-char hex string (32 bytes).
    """
    ikm = f"{guild_slug}/{repo_name}".encode("utf-8")
    salt = b"freenet-git-v1"
    info = b"git-contract-key"

    # RFC 5869 HKDF-SHA256 — same pattern as buzz_identity._hkdf_sha256
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    t = b""
    okm = b""
    counter = 1
    length = 32
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length].hex()


async def push_to_freenet(
    guild_slug: str,
    repo_name: str,
    bundle_bytes: bytes,
) -> Dict[str, Any]:
    """Push a git bundle to Freenet as contract state.

    Encodes the bundle as base64, computes sha256 hash, and stores
    the state JSON via the FreenetService. Gracefully handles the
    Phase F1 stub where the node is not yet running.

    Returns: { contract_key, bundle_hash, pushed_at, status }
    """
    from .service import get_freenet_service

    contract_key = _derive_git_contract_key(guild_slug, repo_name)
    bundle_hash = hashlib.sha256(bundle_bytes).hexdigest()
    bundle_b64 = base64.b64encode(bundle_bytes).decode("ascii")
    pushed_at = datetime.now(timezone.utc).isoformat()

    svc = get_freenet_service()

    # Fetch existing state to preserve push_count and head_commit
    existing: Optional[Dict[str, Any]] = None
    try:
        existing = await svc.get_state(contract_key)
    except Exception:
        pass

    push_count = (existing.get("push_count", 0) + 1) if existing else 1

    state: Dict[str, Any] = {
        "repo_name": repo_name,
        "guild_slug": guild_slug,
        "head_commit": None,       # caller can pass later; populated by head extraction
        "bundle_b64": bundle_b64,
        "bundle_hash": bundle_hash,
        "pushed_at": pushed_at,
        "push_count": push_count,
    }
    state_bytes = json.dumps(state).encode("utf-8")

    status = "queued"
    try:
        ok = await svc.apply_delta(contract_key, state)
        status = "stored" if ok else "queued"
    except RuntimeError as exc:
        # Node not connected — expected in Phase F1
        log.info("Freenet node unavailable for git push (%s/%s): %s", guild_slug, repo_name, exc)
        status = "queued_offline"
    except Exception as exc:
        log.warning("Freenet git push failed (%s/%s): %s", guild_slug, repo_name, exc)
        status = "error"

    return {
        "contract_key": contract_key,
        "bundle_hash": bundle_hash,
        "pushed_at": pushed_at,
        "status": status,
    }


async def get_git_state(
    guild_slug: str,
    repo_name: str,
) -> Optional[Dict[str, Any]]:
    """Fetch current git contract state for a repo.

    Returns the state dict (without bundle_b64) or None if not found
    or the Freenet node is unavailable.
    """
    from .service import get_freenet_service

    contract_key = _derive_git_contract_key(guild_slug, repo_name)
    svc = get_freenet_service()

    try:
        state = await svc.get_state(contract_key)
    except Exception as exc:
        log.debug("Freenet get_git_state failed: %s", exc)
        return None

    if not state:
        return None

    # Strip large bundle payload from the summary response
    return {
        "contract_key": contract_key,
        "repo_name": state.get("repo_name", repo_name),
        "guild_slug": state.get("guild_slug", guild_slug),
        "head_commit": state.get("head_commit"),
        "bundle_hash": state.get("bundle_hash"),
        "pushed_at": state.get("pushed_at"),
        "push_count": state.get("push_count", 0),
    }


async def get_git_bundle_bytes(
    guild_slug: str,
    repo_name: str,
) -> Optional[bytes]:
    """Return raw bundle bytes for download, or None if unavailable."""
    from .service import get_freenet_service

    contract_key = _derive_git_contract_key(guild_slug, repo_name)
    svc = get_freenet_service()

    try:
        state = await svc.get_state(contract_key)
    except Exception:
        return None

    if not state or not state.get("bundle_b64"):
        return None

    try:
        return base64.b64decode(state["bundle_b64"])
    except Exception as exc:
        log.warning("Failed to decode bundle_b64 for %s/%s: %s", guild_slug, repo_name, exc)
        return None


async def list_guild_repos(guild_slug: str) -> List[Dict[str, Any]]:
    """List all repos pushed for a guild.

    Phase F1: FreenetService stores no contract registry, so this returns
    an empty list gracefully. Phase F3+ will implement real listing.
    """
    from .service import get_freenet_service

    svc = get_freenet_service()

    try:
        peers = await svc.get_peers()  # placeholder — no list_contracts() yet
    except Exception:
        pass

    # Phase F1 stub: no contract registry available
    log.debug("list_guild_repos(%s): Phase F1 — no registry yet", guild_slug)
    return []
