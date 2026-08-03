"""Section 25.1 of the buzz_vantage_blueprint extension: vibe set ->
kind:30315 (NIP-38 user status, confirmed real via buzz-core's kind.rs).
Standard, addressable per-author (single current status)."""
import logging

from .buzz_client import BuzzSession
from .buzz_identity import derive_buzz_keypair
from .buzz_registration import RELAY_WS_URL

logger = logging.getLogger(__name__)

KIND_USER_STATUS = 30315


async def publish_vibe_status(agent_id: int, vibe: str, status_code: str) -> None:
    """Never raises -- a failed mirror never blocks the real vibe write."""
    try:
        pk = await derive_buzz_keypair(agent_id)
        sess = BuzzSession(RELAY_WS_URL, pk)
        await sess.connect()
        await sess.authenticate()
        try:
            await sess.publish(KIND_USER_STATUS, vibe, tags=[["d", "general"], ["status", status_code]])
        finally:
            await sess.close()
    except Exception as e:
        logger.warning("buzz_status: publish_vibe_status failed for agent_id=%s: %s", agent_id, e)
