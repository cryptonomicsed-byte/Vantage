"""E2E through the BRIDGE: publish to the channel, wait for the bridge's own
reply event (not our echo), and print it.

v1 of this test returned on the first event carrying the nonce -- which is our
own echo -- so it never actually waited for the bridge. This version ignores
events authored by the sender and resolves only on a message published by the
bridge's pubkey.
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

LOCAL = "ws://localhost:3000"                                  # community 9597206e
CHAN = "bb7d6a9e-640e-475e-9deb-9e182b124388"                  # buzz-e2e-test  # secret-scan:allow
SENDER = 5
BRIDGE = 18


async def main():
    nonce = "brgtest-%s" % uuid.uuid4().hex[:8]
    pk = await derive_buzz_keypair(SENDER)
    me = public_key_xonly_hex(pk)
    bridge_pk = public_key_xonly_hex(await derive_buzz_keypair(BRIDGE))

    print("=" * 78)
    print("  E2E BRIDGE TEST v2 -- waits for the BRIDGE's reply, not our echo")
    print("  channel : %s" % CHAN)
    print("  sender  : agent %d %s" % (SENDER, me[:16]))
    print("  bridge  : agent %d %s" % (BRIDGE, bridge_pk[:16]))
    print("  nonce   : %s" % nonce)
    print("=" * 78)

    sub_sess = BuzzSession(LOCAL, pk)
    await sub_sess.connect()
    await sub_sess.authenticate()
    sub_id = await sub_sess.subscribe(
        [{"kinds": [9], "#h": [CHAN], "since": int(time.time()) - 5}])
    print("  [1] listening (sub %s)" % sub_id)

    reply_holder = {}

    async def listen():
        try:
            while True:
                msg = await sub_sess._recv_json()
                if not (isinstance(msg, list) and msg and msg[0] == "EVENT" and msg[1] == sub_id):
                    if isinstance(msg, list) and msg and msg[0] == "CLOSED":
                        print("      listener CLOSED: %s" % (msg[2] if len(msg) > 2 else ""))
                        return
                    continue
                ev = msg[2]
                author = ev.get("pubkey", "")
                if author == me:
                    print("      (our own echo, ignored)")
                    continue
                if author == bridge_pk:
                    reply_holder["body"] = ev.get("content", "")
                    return
                print("      (other author %s, ignored)" % author[:10])
        except Exception as e:
            print("      listener: %s: %s" % (type(e).__name__, e))

    task = asyncio.create_task(listen())
    await asyncio.sleep(2)

    pub = BuzzSession(LOCAL, pk)
    await pub.connect()
    await pub.authenticate()
    t0 = time.time()
    res = await pub.publish(9, "@%d what can you do? %s" % (BRIDGE, nonce),
                            tags=[["h", CHAN], ["p", bridge_pk], ["vt", "say"]])
    print("  [2] published -> ack ok=%s" % str(res.get("ack"))[:80])
    await pub.close()

    print("  [3] waiting up to 60s for the bridge's REPLY event...")
    try:
        await asyncio.wait_for(task, timeout=60)
        print("\n  ✅ BRIDGE REPLIED in %.2fs" % (time.time() - t0))
        print("  ─" * 38)
        print(reply_holder.get("body", "")[:900])
        print("  ─" * 38)
    except asyncio.TimeoutError:
        print("\n  ❌ no bridge-authored event within 60s")
        task.cancel()

    try:
        await sub_sess.close()
    except Exception:
        pass


asyncio.run(main())
