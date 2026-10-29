"""TEST 2 — POST /query with real NIP-98 auth.

Answers the user's step-2 question: does an authenticated #p query return
Bob-addressed events over HTTP?

NIP-98 per crates/buzz-auth/src/nip98.rs and buzz-acp/src/relay.rs:
  signed kind:27235 event, tags:
    ["u", <url>] ["method", <method>] ["nonce", <uuid>] ["payload", sha256hex(body)]
  header: Authorization: Nostr <base64(json(event))>

Body is a JSON ARRAY of filters (buzz-acp query() serializes &[Filter]).

This is a HISTORICAL query. It proves the relay authorizes and understands the
filter. It does NOT prove real-time delivery — that is a separate test.
"""
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, "/opt/ares/Vantage")

_pid = subprocess.run(["systemctl", "show", "vantage", "-p", "MainPID", "--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for _e in open(f"/proc/{_pid}/environ", "rb").read().decode(errors="replace").split("\0"):
        if "=" in _e:
            _k, _v = _e.split("=", 1)
            os.environ.setdefault(_k, _v)

from backend.buzz_client import build_event                      # noqa: E402
from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex  # noqa: E402

BASE = "http://127.0.0.1:3000"
HOST = "omokoda.duckdns.org:3443"       # the PROD community (must match exactly)
ME_ID, OTHER_ID = 5, 443


def nip98_header(pk, method: str, url: str, body: bytes) -> str:
    tags = [
        ["u", url],
        ["method", method],
        ["nonce", str(uuid.uuid4())],
        ["payload", hashlib.sha256(body).hexdigest()],
    ]
    ev = build_event(pk, 27235, "", tags=tags)
    return "Nostr " + base64.b64encode(json.dumps(ev).encode()).decode()


def do_query(pk, filters, host=HOST, url_used=None):
    body = json.dumps(filters).encode()
    # The relay validates the `u` tag against the URL it derives from the Host
    # header (host-derived community boundary). It expected:
    #   https://omokoda.duckdns.org:3443/query
    # not the loopback address we actually connect to.
    url = url_used or f"https://{host}/query"
    auth = nip98_header(pk, "POST", url, body)
    req = urllib.request.Request(f"{BASE}/query", data=body, method="POST")
    req.add_header("Host", host)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode()[:900]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


async def main():
    pk = await derive_buzz_keypair(ME_ID)
    me = public_key_xonly_hex(pk)
    other = public_key_xonly_hex(await derive_buzz_keypair(OTHER_ID))
    print("=" * 70)
    print(f"  POST /query  Host: {HOST}")
    print(f"  me={me[:16]}…   target={other[:16]}…")
    print("=" * 70)

    cases = [
        ("A. #p = another agent (THE QUESTION)",
         [{"kinds": [9, 1], "#p": [other]}]),
        ("B. #p = self",
         [{"kinds": [9, 1], "#p": [me]}]),
        ("C. #p = ghost (control)",
         [{"kinds": [9, 1], "#p": ["ab" * 32]}]),
        ("D. no #p (baseline)",
         [{"kinds": [9, 1]}]),
    ]
    for label, filt in cases:
        status, body = do_query(pk, filt)
        try:
            parsed = json.loads(body)
            n = len(parsed) if isinstance(parsed, list) else parsed
            if isinstance(parsed, list):
                ps = set()
                for e in parsed[:20]:
                    for t in (e.get("tags") or []):
                        if t and t[0] == "p" and len(t) > 1:
                            ps.add(t[1][:14])
                extra = f"  p_tags seen: {sorted(ps)}" if ps else ""
                print(f"\n  {label}\n    HTTP {status}  ->  {n} event(s){extra}")
            else:
                print(f"\n  {label}\n    HTTP {status}  ->  {parsed}")
        except Exception:
            print(f"\n  {label}\n    HTTP {status}  ->  {body[:260]}")

    print("\n" + "=" * 70)
    print("  If A returns events carrying the other agent's p tag: HTTP path")
    print("  authorized for cross-agent #p reads. (Historical only.)")
    print("=" * 70)


asyncio.run(main())
