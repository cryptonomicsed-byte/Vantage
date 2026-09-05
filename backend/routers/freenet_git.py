"""Freenet Git Bridge router — Phase F6.

Endpoints for pushing, fetching and downloading git bundles stored
as Freenet contract state.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ..deps import get_agent
from ..freenet.git_bridge import (
    get_git_bundle_bytes,
    get_git_state,
    list_guild_repos,
    push_to_freenet,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/freenet/git", tags=["freenet-git"])


@router.post("/{guild_slug}/{repo_name}", summary="Push a git bundle to Freenet")
async def push_git_bundle(
    guild_slug: str,
    repo_name: str,
    request: Request,
    file: Optional[UploadFile] = File(None),
    agent: dict = Depends(get_agent),
):
    """Push a git bundle for a guild repository.

    Accepts either:
    - multipart `file` upload (raw .bundle bytes)
    - JSON body `{ "bundle_b64": "<base64>" }`

    Returns the push result with contract_key, bundle_hash, pushed_at and status.
    """
    bundle_bytes: Optional[bytes] = None

    if file is not None:
        bundle_bytes = await file.read()
    else:
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                body = await request.json()
            except Exception:
                body = {}
            b64 = body.get("bundle_b64")
            if b64:
                try:
                    bundle_bytes = base64.b64decode(b64)
                except Exception:
                    raise HTTPException(400, "Invalid base64 in bundle_b64")

    if not bundle_bytes:
        raise HTTPException(400, "Provide a multipart 'file' upload or JSON body with 'bundle_b64'")

    result = await push_to_freenet(guild_slug, repo_name, bundle_bytes)
    return result


@router.get("/{guild_slug}/{repo_name}/download", summary="Download a git bundle from Freenet")
async def download_git_bundle(
    guild_slug: str,
    repo_name: str,
    agent: dict = Depends(get_agent),
):
    """Download the stored git bundle as binary application/octet-stream.

    Returns 503 if the Freenet node is offline, 404 if no bundle has been pushed.
    """
    bundle_bytes = await get_git_bundle_bytes(guild_slug, repo_name)
    if bundle_bytes is None:
        state = await get_git_state(guild_slug, repo_name)
        if state is None:
            raise HTTPException(404, f"No git bundle found for {guild_slug}/{repo_name}")
        raise HTTPException(503, "Freenet node offline — bundle metadata found but bytes unavailable")

    filename = f"{repo_name}.bundle"
    return Response(
        content=bundle_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{guild_slug}/{repo_name}", summary="Get git repo state from Freenet")
async def get_repo_state(guild_slug: str, repo_name: str):
    """Return current contract state for a specific repo.

    Returns { head_commit, bundle_hash, pushed_at, contract_key, push_count }
    or 404 if not found. No auth required.
    """
    state = await get_git_state(guild_slug, repo_name)
    if state is None:
        raise HTTPException(404, f"No git state found for {guild_slug}/{repo_name}")
    return state


@router.get("/{guild_slug}", summary="List Freenet git repos for a guild")
async def list_repos(guild_slug: str):
    """List all known repos pushed for a guild.

    Returns [{ repo_name, head_commit, bundle_hash, pushed_at }].
    Phase F1: returns an empty list (no contract registry yet).
    No auth required.
    """
    repos = await list_guild_repos(guild_slug)
    return repos
