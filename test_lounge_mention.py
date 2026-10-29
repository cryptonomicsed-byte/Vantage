"""End-to-end: @mention agent 443 in lounge-general and wait for its reply.

Mirrors what the user's client actually does:
  channel  = lounge-general (b96b97cd) in community 8557317a
  endpoint = wss://omokoda.duckdns.org:3443
  tags     = [["h",chan], ["p",<443 pubkey>], ["vt","say"]]
"""
import asyncio
import os
import subprocess
import sys
import time
import uuid

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

URL = "wss://omokoda.duckdns.org:3443"
CHAN = "b96b97cd-7161-4c73-925e-e6de3725b325"      # lounge-general, community 8557317a  # secret-scan:allow
SENDER = 5          # you
TARGET = 443        # probe-register-e2e (the bridge serves this now)


async def main():
    nonce = "lounge-%s" % uuid.uuid4().hex[:8]
    pk = await derive_buzz_keypair(SENDER)
    me = public_key_xonly_hex(pk)
    tp = public_key_xonly_hex(await derive_buzz_keypair(TARGET))
    print("=" * 78)
    print("  LOUNGE @MENTION TEST")
    print("  channel : lounge-general %s" % CHAN)
    print("  you     : agent %d %s" % (SENDER, me[:16]))
    print("  target  : agent %d %s" % (TARGET, tp[:16]))
    print("=" * 78)

    sub = BuzzSession(URL, pk)
    await sub.connect()
    await sub.authenticate()
    sid = await sub.subscribe([{"kinds": [9], "#h": [CHAN], "since": int(time.time()) - 5}])
    print("  [1] listening")

    holder = {}

    async def listen():
        try:
            while True:
                msg = await sub._recv_json()
                if isinstance(msg, list) and msg and msg[0] == "CLOSED":
                    print("      listener CLOSED:", msg[2] if len(msg) > 2 else "")
                    return
                if not (isinstance(msg, list) and len(msg) > 2 and msg[0] == "EVENT" and msg[1] == sid):
                    continue
                ev = msg[2]
                a = ev.get("pubkey", "")
                if a == me:
                    continue
                print("      reply from %s" % a[:14])
                holder["body"] = ev.get("content", "")
                return
        except Exception as e:
            print("      listener:", type(e).__name__, e)

    t = asyncio.create_task(listen())
    await asyncio.sleep(2)

    pub = BuzzSession(URL, pk)
    await pub.connect()
    await pub.authenticate()
    t0 = time.time()
    res = await pub.publish(9, "@%d what can you do? %s" % (TARGET, nonce),
                            tags=[["h", CHAN], ["p", tp], ["vg", "lounge", "general"], ["vt", "say"]])
    print("  [2] sent mention -> ack=%s" % str(res.get("ack"))[:70])
    await pub.close()

    print("  [3] waiting up to 60s for agent %d's reply..." % TARGET)
    try:
        await asyncio.wait_for(t, timeout=60)
        print("\n  ✅ REPLIED in %.2fs" % (time.time() - t0))
        print("  " + "-" * 62)
        print(holder.get("body", "")[:700])
        print("  " + "-" * 62)
    except asyncio.TimeoutError:
        print("\n  ❌ no reply in 60s")
        t.cancel()
    try:
        await sub.close()
    except Exception:
        pass


asyncio.run(main())
