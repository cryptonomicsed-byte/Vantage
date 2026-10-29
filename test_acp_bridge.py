"""ACP BRIDGE DIAGNOSIS -- why ares-vantage-buzz-acp.service is a silent zombie.

The bridge (backend/buzz_acp_bridge.py) does:

    sub = await sess.subscribe([{"kinds": [9], "#h": [channel_id], "since": now}])
    while ...:
        msg = await sess._recv_json()
        if msg[0] != "EVENT" or msg[1] != sub:
            continue                      # <-- CLOSED frames silently discarded

Its systemd env:
    BUZZ_ACP_AGENT_ID   = 18
    BUZZ_ACP_RELAY_URL  = wss://omokoda.duckdns.org:3443   -> community 8557317a
    BUZZ_ACP_CHANNEL_ID = bb7d6a9e-...                     -> community 9597206e

Cross-community: the endpoint's community does not own that channel, so the
REQ is rejected with CLOSED. The loop discards CLOSED and waits forever, the
socket eventually dies on keepalive, and run_bridge_forever reconnects -- the
crash loop visible in the journal.

The code's OWN default fallback is ws://localhost:3000, i.e. community
9597206e -- the one that actually owns the channel.

This script proves both halves with agent 18 (the bridge's own identity).
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, "/opt/ares/Vantage")

_pid = subprocess.run(["systemctl", "show", "vantage", "-p", "MainPID", "--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for _e in open("/proc/%s/environ" % _pid, "rb").read().decode(errors="replace").split("\0"):
        if "=" in _e:
            _k, _v = _e.split("=", 1)
            os.environ.setdefault(_k, _v)

from backend.buzz_client import BuzzSession                                   # noqa: E402
from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex   # noqa: E402

AGENT = 18                                                    # the bridge's identity
CHAN = "bb7d6a9e-640e-475e-9deb-9e182b124388"                 # buzz-e2e-test  # secret-scan:allow
PUBLIC = "wss://omokoda.duckdns.org:3443"                     # community 8557317a
LOCAL = "ws://localhost:3000"                                 # community 9597206e


async def probe(url, label):
    pk = await derive_buzz_keypair(AGENT)
    s = BuzzSession(url, pk)
    try:
        await s.connect()
        await s.authenticate()
    except Exception as e:
        print("  %-34s CONNECT/AUTH FAIL %s: %s" % (label, type(e).__name__, e))
        return None

    sub = await s.subscribe([{"kinds": [9], "#h": [CHAN], "since": 0}])
    # Read frames EXACTLY as the bridge's loop does, but report what it discards.
    outcome = None
    try:
        for _ in range(6):
            msg = await asyncio.wait_for(s._recv_json(), timeout=15)
            kind = msg[0] if isinstance(msg, list) and msg else "?"
            if kind == "EVENT" and msg[1] == sub:
                outcome = "EVENT (would be handled)"
                break
            if kind == "CLOSED" and msg[1] == sub:
                outcome = "CLOSED -> %s" % (msg[2] if len(msg) > 2 else "")
                break
            if kind == "EOSE" and msg[1] == sub:
                outcome = "EOSE (no backlog; live sub OK)"
                break
            outcome = "ignored frame %s" % kind
    except asyncio.TimeoutError:
        outcome = "TIMEOUT (silent -- exactly the zombie state)"
    except Exception as e:
        outcome = "%s: %s" % (type(e).__name__, e)
    try:
        await s.close()
    except Exception:
        pass

    print("  %-34s -> %s" % (label, outcome))
    return outcome


async def main():
    print("=" * 84)
    print("  ACP BRIDGE DIAGNOSIS -- agent 18, channel buzz-e2e-test")
    print("  channel %s (community 9597206e)" % CHAN)
    print("  agent 18 pubkey: %s" % public_key_xonly_hex(await derive_buzz_keypair(AGENT)))
    print("=" * 84)
    print("")
    print("  as CONFIGURED (BUZZ_ACP_RELAY_URL):")
    a = await probe(PUBLIC, "wss://omokoda.duckdns.org:3443")
    print("")
    print("  as the code's OWN default would have it:")
    b = await probe(LOCAL, "ws://localhost:3000")
    print("")
    print("=" * 84)
    print("  VERDICT")
    print("=" * 84)
    if a and "CLOSED" in a and b and "EVENT" in b or (b and "EOSE" in b):
        print("  The configured endpoint REJECTS the subscription; the localhost")
        print("  endpoint the code defaults to ACCEPTS it. The bridge is pointed")
        print("  at the wrong community, and discards the CLOSED frame that says so.")
    print("=" * 84)


asyncio.run(main())
