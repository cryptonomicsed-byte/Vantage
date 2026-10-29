"""TEST 3b — live delivery with the CORRECT filter shape.

Test 3 failed because a global (#p only, no #h) subscription is by design
never given channel-scoped events. Channel messages are channel-scoped.

Correct shape: channel-scoped (#h) AND #p. Registration lands in
channel_kind_index; the #p tag is then applied by filters_match at fan-out.
"""
import asyncio, json, os, subprocess, sys, time, uuid
sys.path.insert(0, "/opt/ares/Vantage")
_pid = subprocess.run(["systemctl","show","vantage","-p","MainPID","--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for e in open(f"/proc/{_pid}/environ","rb").read().decode(errors="replace").split("\0"):
        if "=" in e:
            k,v = e.split("=",1); os.environ.setdefault(k,v)
from backend.buzz_client import BuzzSession, build_event
from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex
from backend.buzz_registration import RELAY_WS_URL

SUB, TGT = 5, 443
LOUNGE = "b96b97cd-7161-4c73-925e-e6de3725b325"  # secret-scan:allow

async def main():
    pk = await derive_buzz_keypair(SUB)
    tgt = public_key_xonly_hex(await derive_buzz_keypair(TGT))
    nonce = f"hp-{uuid.uuid4().hex[:10]}"

    sub = BuzzSession(RELAY_WS_URL, pk)
    await sub.connect(); await sub.authenticate()
    filt = [{"kinds": [9, 1], "#h": [LOUNGE], "#p": [tgt]}]
    sid = await sub.subscribe(filt)
    print(f"  filter: {json.dumps(filt)}")
    print(f"  sub_id={sid}  nonce={nonce}")
    await asyncio.sleep(3)

    got, hit = [], asyncio.Event()

    async def listen():
        try:
            async for ev in sub.stream_events(sid):
                got.append(ev)
                if nonce in json.dumps(ev):
                    print(f"      >>> ARRIVED LIVE: {ev.get('id','')[:16]}…")
                    hit.set(); return
        except Exception as e:
            print(f"      listener: {type(e).__name__}: {e}")

    t = asyncio.create_task(listen())
    await asyncio.sleep(2)

    pub = BuzzSession(RELAY_WS_URL, pk)
    await pub.connect(); await pub.authenticate()
    t0 = time.time()
    r = await pub.publish(9, f"@{TGT} {nonce}",
                          tags=[["h", LOUNGE], ["p", tgt], ["vt", "say"]])
    print(f"  published -> {json.dumps(r)[:110]}")
    await pub.close()

    try:
        await asyncio.wait_for(hit.wait(), timeout=20)
        print(f"\n  ✅ LIVE DELIVERY CONFIRMED — pushed in {time.time()-t0:.2f}s")
        print("     channel-scoped + #p is the correct inbox shape.")
    except asyncio.TimeoutError:
        print(f"\n  ❌ still not delivered. events seen: {len(got)}")
        t.cancel()
    await sub.close()

asyncio.run(main())
