"""TEST 3 — LIVE delivery. The test POST /query cannot do.

POST /query proved the relay authorizes and applies a cross-agent #p filter.
It is a HISTORICAL query. It says nothing about whether an event addressed to
Bob is PUSHED to Bob's instance the moment it is published.

This opens a live WebSocket subscription for another agent's #p, then
publishes an event addressed to that agent WHILE THE SUBSCRIPTION IS OPEN,
and reports whether it arrives in real time.

Tagged with a nonce so the delivered event cannot be confused with backlog.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

sys.path.insert(0, "/opt/ares/Vantage")

_pid = subprocess.run(["systemctl", "show", "vantage", "-p", "MainPID", "--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for _e in open(f"/proc/{_pid}/environ", "rb").read().decode(errors="replace").split("\0"):
        if "=" in _e:
            _k, _v = _e.split("=", 1)
            os.environ.setdefault(_k, _v)

from backend.buzz_client import BuzzSession, build_event            # noqa: E402
from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex  # noqa: E402
from backend.buzz_registration import RELAY_WS_URL                   # noqa: E402

SUBSCRIBER = 5      # listens for mentions of 443
TARGET = 443        # the agent being mentioned
LOUNGE = "b96b97cd-7161-4c73-925e-e6de3725b325"   # lounge/general buzz channel id  # secret-scan:allow


async def main():
    pk_sub = await derive_buzz_keypair(SUBSCRIBER)
    me = public_key_xonly_hex(pk_sub)
    pk_t = await derive_buzz_keypair(TARGET)
    target = public_key_xonly_hex(pk_t)
    nonce = f"livedelivery-{uuid.uuid4().hex[:10]}"

    print("=" * 72)
    print(f"  relay      : {RELAY_WS_URL}")
    print(f"  subscriber : agent {SUBSCRIBER} ({me[:16]}…)")
    print(f"  target     : agent {TARGET} ({target[:16]}…)")
    print(f"  nonce      : {nonce}")
    print("=" * 72)

    # ── 1. open the live subscription BEFORE anything is published
    sub_sess = BuzzSession(RELAY_WS_URL, pk_sub)
    await sub_sess.connect()
    await sub_sess.authenticate()
    sub_id = await sub_sess.subscribe(
        [{"kinds": [9, 1], "#p": [target]}]        # no #h: catch mentions anywhere
    )
    print(f"\n  [1] live subscription open: sub_id={sub_id}")
    print(f"      filter: kinds=[9,1] #p=[{target[:16]}…]   (no #h)")
    await asyncio.sleep(3)      # let EOSE/backlog drain

    received = []

    async def listen():
        try:
            async for ev in sub_sess.stream_events(sub_id):
                received.append(ev)
                if nonce in json.dumps(ev):
                    print(f"      >>> NONCE EVENT ARRIVED: {ev.get('id','')[:16]}…")
                    return
        except Exception as e:
            print(f"      listener ended: {type(e).__name__}: {e}")

    listen_task = asyncio.create_task(listen())
    await asyncio.sleep(2)

    # ── 2. publish an event addressed to TARGET, while the sub is live
    pub_sess = BuzzSession(RELAY_WS_URL, pk_sub)
    await pub_sess.connect()
    await pub_sess.authenticate()
    t0 = time.time()
    ev = build_event(pk_sub, 9, f"@{TARGET} {nonce}",
                     tags=[["h", LOUNGE], ["p", target], ["vt", "say"]])
    try:
        res = await pub_sess.publish(9, f"@{TARGET} {nonce}",
                                     tags=[["h", LOUNGE], ["p", target], ["vt", "say"]])
        print(f"\n  [2] published at t=0  -> {json.dumps(res)[:140]}")
    except Exception as e:
        print(f"\n  [2] publish FAILED: {type(e).__name__}: {e}")
    await pub_sess.close()

    # ── 3. wait for it to be pushed
    print("  [3] waiting up to 20s for the push…")
    try:
        await asyncio.wait_for(listen_task, timeout=20)
        latency = time.time() - t0
        print(f"\n  ✅ LIVE DELIVERY CONFIRMED — pushed in {latency:.2f}s")
        print(f"     events seen on the live subscription: {len(received)}")
    except asyncio.TimeoutError:
        print(f"\n  ❌ NOT DELIVERED within 20s")
        print(f"     events seen on the live subscription: {len(received)}")
        print("     -> the live #p push path did not fire; POST /query is")
        print("        historical only, as suspected.")
        listen_task.cancel()

    try:
        await sub_sess.close()
    except Exception:
        pass


asyncio.run(main())
