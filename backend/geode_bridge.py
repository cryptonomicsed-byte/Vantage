"""
geode-buzz-bridge: authenticates with buzz relay (NIP-42), subscribes to
non-p-gated event kinds, and forwards events to geode via NIP-FE HTTP POST.

P-gated kinds (excluded): 1059, 24200, 44100, 44101, 30622, 44200
"""
import asyncio
import json
import logging
import os
import sys
import time

import httpx
import websockets

sys.path.insert(0, "/opt/ares/Vantage")

from backend.buzz_identity import derive_instance_keypair, public_key_xonly_hex
from backend.nostr.client import NostrSession, build_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s [geode-bridge] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BUZZ_WS = os.environ.get("RELAY_WS_URL", "wss://omokoda.duckdns.org:3443/relay")
GEODE_HTTP = os.environ.get("GEODE_HTTP_URL", "http://127.0.0.1:3444")
RECONNECT_DELAY = 30

# All standard + Buzz kinds EXCEPT p-gated (1059/gift-wrap, 24200, 44100, 44101, 30622, 44200)
# and author-only kinds. Covers NIP-01/29/23/25/33/50/90 + Buzz community/channel kinds.
SUBSCRIBE_KINDS = [
    0,      # metadata
    1,      # note
    3,      # contacts
    5,      # deletion
    7,      # reaction
    8,      # badge award
    9,      # channel message (NIP-29)
    10,     # contact list
    11,     # channel create (NIP-29)
    12,     # channel metadata (NIP-29)
    16,     # generic repost
    17,     # reaction
    23,     # long-form article (NIP-23)
    25,     # reaction
    29,     # group thread
    30,     # group thread reply
    40,     # channel create (legacy)
    41,     # channel metadata (legacy)
    42,     # channel message (legacy)
    1311,   # live chat message
    5000, 5001, 5002, 5003, 5004,   # DVM job requests
    6000, 6001, 6002, 6003, 6004,   # DVM job results
    7000,   # DVM feedback
    9000, 9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008, 9009,  # NIP-29 group admin
    10009,  # NIP-29 relay list
    10050,  # DM relay list
    11316, 11317,  # cordn coordinator announcement
    25910,  # cordn MCP tool call
    30000, 30001, 30008, 30009, 30023, 30024, 30078, 30311,  # addressable
    30443, 444, 445, 447, 448, 449,  # MLS/marmot
    31020, 31030,  # Witness attestation
    33534,  # NIP-43 role definition
    13534,  # NIP-43 member list
    8000, 8001,  # NIP-43 member added/removed
    28934, 28936,  # NIP-43 join/leave
]


async def ingest_event(client: httpx.AsyncClient, event: dict) -> None:
    body = json.dumps(["EVENT", event], separators=(",", ":"))
    try:
        resp = await client.post(
            GEODE_HTTP,
            content=body.encode(),
            headers={"Content-Type": "application/nostr+json"},
            timeout=5.0,
        )
        if resp.status_code not in (200, 201):
            log.debug("geode %s for kind %s", resp.status_code, event.get("kind"))
    except Exception as exc:
        log.warning("geode ingest error: %s", exc)


async def run_bridge():
    pk = await derive_instance_keypair("vantage")
    pubkey = public_key_xonly_hex(pk)
    log.info("Bridge identity: %s", pubkey)

    async with httpx.AsyncClient() as http:
        while True:
            try:
                log.info("Connecting to %s", BUZZ_WS)
                session = NostrSession(BUZZ_WS, pk)
                await session.connect()
                await session.authenticate()
                log.info("NIP-42 auth complete")

                sub_id = "geode-bridge"
                await session.ws.send(json.dumps([
                    "REQ", sub_id,
                    {"kinds": SUBSCRIBE_KINDS, "limit": 500}
                ]))
                log.info("Subscribed to %d kinds (limit=500 backfill)", len(SUBSCRIBE_KINDS))

                count = 0
                async for raw in session.ws:
                    msg = json.loads(raw)
                    if msg[0] == "EVENT":
                        await ingest_event(http, msg[2])
                        count += 1
                        if count % 100 == 0:
                            log.info("Forwarded %d events to geode", count)
                    elif msg[0] == "EOSE":
                        log.info("Backfill complete (%d events). Live streaming...", count)
                    elif msg[0] in ("NOTICE", "CLOSED"):
                        log.warning("relay msg: %s", msg)
                        if msg[0] == "CLOSED":
                            break

            except Exception as exc:
                log.warning("Disconnected: %s — retry in %ds", exc, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(run_bridge())
