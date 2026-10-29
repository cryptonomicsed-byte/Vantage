"""MEMBERSHIP TEST v4 -- community scoping, one endpoint, two channels.

v2 returned "restricted: not a channel member" for every channel including an
open one with 151k events. Diagnosis:

    communities:
      9597206e-a38b-45e1-ac76-a9e7f725b8ed -> host = "localhost:3000"  # secret-scan:allow
      8557317a-e294-4ac5-baa7-3196bec8b3a5 -> host = "omokoda.duckdns.org:3443"  # secret-scan:allow

Two communities on one relay, resolved from the Host header. buzz-e2e-test
lives in 9597206e; the public endpoint resolves to 8557317a. So a REQ for e2e
from the public endpoint asks the WRONG community about that channel.

Positive control: 'hell' is open, lives in 8557317a (the public community),
and agent 5 IS a member there (verified with the relay's exact is_member SQL
against community 8557317a -> COUNT = 1).

Same endpoint, same pubkey, same relay process. Only the channel's community
differs. If community scoping is the answer:
    e2e   (community 9597206e) -> CLOSED "not a channel member"
    hell  (community 8557317a) -> DATA
    lounge(community 8557317a) -> DATA
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
from backend.buzz_registration import RELAY_WS_URL                            # noqa: E402

E2E = "bb7d6a9e-640e-475e-9deb-9e182b124388"      # community 9597206e (localhost:3000)  # secret-scan:allow
HELL = "3e8bb9ec-a02f-4ac3-b49d-464ba0530f8e"     # community 8557317a (public)  # secret-scan:allow
LOUNGE = "b96b97cd-7161-4c73-925e-e6de3725b325"   # community 8557317a (public)  # secret-scan:allow


async def probe(agent_id, channel, chname, expect):
    pk = await derive_buzz_keypair(agent_id)
    s = BuzzSession(RELAY_WS_URL, pk)
    try:
        await s.connect()
        await s.authenticate()
    except Exception as e:
        print("  %-10s CONNECT/AUTH FAIL %s: %s" % (chname, type(e).__name__, e))
        return "CONNFAIL"

    sub_id = await s.subscribe([{"kinds": [9, 1], "#h": [channel], "limit": 3}])
    try:
        events = await asyncio.wait_for(s.recv_until_eose(sub_id, max_events=3), timeout=20)
        out = "DATA(%d)" % len(events)
    except asyncio.TimeoutError:
        out = "TIMEOUT"
    except Exception as e:
        out = str(e).split("closed by relay: ")[-1][:44]
    try:
        await s.close()
    except Exception:
        pass

    mark = "ok " if (expect in out) else "!! "
    print("  %s%-10s -> %-46s (expected: %s)" % (mark, chname, out, expect))
    return out


async def main():
    print("=" * 84)
    print("  MEMBERSHIP TEST v4 -- community scoping on ONE endpoint")
    print("  relay: %s" % RELAY_WS_URL)
    print("  agent 5: %s  (member of e2e in 9597206e; of hell in 8557317a)"
          % public_key_xonly_hex(await derive_buzz_keypair(5)))
    print("=" * 84)
    print("")
    r1 = await probe(5, E2E, "e2e", "not a channel member")
    r2 = await probe(5, HELL, "hell", "DATA")
    r3 = await probe(5, LOUNGE, "lounge", "DATA")
    print("")
    print("=" * 84)
    print("  RESULT")
    print("=" * 84)
    print("  e2e    (community 9597206e) -> %s" % r1)
    print("  hell   (community 8557317a) -> %s" % r2)
    print("  lounge (community 8557317a) -> %s" % r3)
    print("")
    if "not a channel member" in r1 and "DATA" in r2:
        print("  => CONFIRMED. Identical endpoint, identical pubkey, identical")
        print("     process. The ONLY difference is the channel's community.")
        print("     Channel access does not cross communities, and the community")
        print("     is resolved from the Host header.")
    print("=" * 84)


asyncio.run(main())
