"""Section 17.1 of the buzz_vantage_blueprint: repo create -> a real
NIP-34 kind:30617 repo announcement (confirmed via buzz-sdk's
build_repo_announcement -- exact tag shape: d=repo_id, name,
description, clone (multi-value), web, relays).
"""
import logging

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair
from .buzz_pairing import PUBLIC_RELAY_WS_URL
from .buzz_registration import RELAY_WS_URL

logger = logging.getLogger(__name__)

KIND_GIT_REPO_ANNOUNCEMENT = 30617


async def announce_repo(agent_id: int, repo_id: str, name: str, description: str, clone_url: str, web_url: str) -> None:
    """Never raises -- a failed announcement mirror shouldn't block real
    repo creation on Vantage's own Gitea instance."""
    tags = [["d", repo_id[:128]]]
    if name:
        tags.append(["name", name[:128]])
    if description:
        tags.append(["description", description[:1024]])
    if clone_url:
        tags.append(["clone", clone_url[:512]])
    if web_url:
        tags.append(["web", web_url[:512]])
    tags.append(["relays", PUBLIC_RELAY_WS_URL])

    try:
        pk = await derive_buzz_keypair(agent_id)
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            result = await sess.publish(KIND_GIT_REPO_ANNOUNCEMENT, "", tags=tags)
            if not result["ack"][2]:
                logger.warning("buzz_git: repo announcement rejected for %s: %s", repo_id, result["ack"])
        finally:
            await sess.close()
    except Exception as e:
        logger.warning("buzz_git: announce_repo failed for %s: %s", repo_id, e)
