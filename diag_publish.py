"""Was the live-test event STORED but not pushed, or never stored at all?"""
import base64, hashlib, json, os, subprocess, sys, urllib.error, urllib.request, uuid
sys.path.insert(0, "/opt/ares/Vantage")
_pid = subprocess.run(["systemctl","show","vantage","-p","MainPID","--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for e in open(f"/proc/{_pid}/environ","rb").read().decode(errors="replace").split("\0"):
        if "=" in e:
            k,v = e.split("=",1); os.environ.setdefault(k,v)
from backend.buzz_client import build_event
from backend.buzz_identity import derive_buzz_keypair

EV = "aadcf533b1fb03f11469c1907074cd4f11d1e9953a2d24e18428d8ff02056534"  # secret-scan:allow
HOST = "omokoda.duckdns.org:3443"
BASE = "http://127.0.0.1:3000"

def query(pk, filters):
    body = json.dumps(filters).encode()
    tags = [["u", f"https://{HOST}/query"], ["method","POST"],
            ["nonce", str(uuid.uuid4())], ["payload", hashlib.sha256(body).hexdigest()]]
    ev = build_event(pk, 27235, "", tags=tags)
    req = urllib.request.Request(f"{BASE}/query", data=body, method="POST")
    req.add_header("Host", HOST); req.add_header("Content-Type","application/json")
    req.add_header("Authorization","Nostr "+base64.b64encode(json.dumps(ev).encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=25) as r: return r.status, r.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:400]

async def main():
    pk = await derive_buzz_keypair(5)
    s, b = query(pk, [{"ids": [EV]}])
    print(f"  query by id  -> HTTP {s}")
    try:
        d = json.loads(b)
        print(f"  STORED: {'YES — ' + str(len(d)) + ' event(s)' if d else 'NO — event was never accepted'}")
        if d:
            e = d[0]
            print(f"    kind={e.get('kind')} pubkey={e.get('pubkey','')[:16]}…")
            print(f"    tags={[t[:2] for t in e.get('tags',[])]}")
    except Exception:
        print("  ", b[:300])
    # also: everything recently addressed to 443
    s2, b2 = query(pk, [{"kinds":[9,1], "#p":["d0129105eebe291c829ae90ceefc784b4258141a69ba8a38b461d69df2cf85f1"]}])  # secret-scan:allow
    try:
        d2 = json.loads(b2)
        print(f"\n  all events with #p=443: {len(d2)}")
        for e in d2[:12]:
            print(f"    {e.get('id','')[:16]}…  kind={e.get('kind')}  {str(e.get('content'))[:40]!r}")
    except Exception:
        print("  ", b2[:200])

import asyncio; asyncio.run(main())
