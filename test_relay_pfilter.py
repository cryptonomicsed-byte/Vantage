"""TEST 0b — does the #p filter actually NARROW, or is it just accepted?

TEST 0 proved a connection may subscribe #p for another agent's pubkey.
That is necessary but not sufficient. An index that accepts the filter and
then ignores it would return everything, and the inbox would have to filter
client-side.

This asks it directly: subscribe with a pubkey that CANNOT have matches and
see whether the relay returns zero events. If it returns the same events as
a real pubkey, the filter is decorative.

Also prints what actually comes back, so "5 events" can be told apart from
"5 events that genuinely carry that p tag".
"""
import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, "/opt/ares/Vantage")

_pid = subprocess.run(["systemctl", "show", "vantage", "-p", "MainPID", "--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for _e in open(f"/proc/{_pid}/environ", "rb").read().decode(errors="replace").split("\0"):
        if "=" in _e:
            _k, _v = _e.split("=", 1)
            os.environ.setdefault(_k, _v)

from backend.buzz_client import BuzzSession  # noqa: E402
from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex  # noqa: E402
from backend.buzz_registration import RELAY_WS_URL  # noqa: E402

ME_ID = 5
OTHER_ID = 443
# A pubkey that is valid hex but belongs to nobody, so it cannot appear in a
# p tag on this relay.
GHOST = "ab" * 32


def p_tags_of(ev: dict) -> list:
    return [t[1] for t in (ev.get("tags") or []) if t and t[0] == "p" and len(t) > 1]


async def probe(label: str, filters: list, show: int = 3) -> int:
    pk = await derive_buzz_keypair(ME_ID)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    sub = await sess.subscribe(filters)
    print(f"\n── {label}")
    try:
        evs = await asyncio.wait_for(sess.recv_until_eose(sub, max_events=200), timeout=25)
        print(f"   returned {len(evs)} event(s)")
        for e in evs[:show]:
            print(f"     kind={e.get('kind')} id={str(e.get('id'))[:12]}… "
                  f"p_tags={[x[:12] + '…' for x in p_tags_of(e)] or 'NONE'}")
        return len(evs)
    except asyncio.TimeoutError:
        print("   ⚠ timed out")
        return -1
    except RuntimeError as e:
        print(f"   ❌ {e}")
        return -2
    finally:
        await sess.close()


async def main() -> None:
    me = public_key_xonly_hex(await derive_buzz_keypair(ME_ID))
    other = public_key_xonly_hex(await derive_buzz_keypair(OTHER_ID))
    print("=" * 70)
    print(f"  me={me[:16]}…  other={other[:16]}…  ghost={GHOST[:16]}…")
    print(f"  relay={RELAY_WS_URL}")
    print("=" * 70)

    n_none = await probe("A. NO #p at all (baseline: everything)", [{"kinds": [9, 1]}])
    n_me = await probe("B. #p = me", [{"kinds": [9, 1], "#p": [me]}])
    n_other = await probe("C. #p = another agent", [{"kinds": [9, 1], "#p": [other]}])
    n_ghost = await probe("D. #p = GHOST (impossible pubkey)", [{"kinds": [9, 1], "#p": [GHOST]}])

    print("\n" + "=" * 70)
    print("  VERDICT")
    print(f"   no-filter : {n_none}")
    print(f"   p=me      : {n_me}")
    print(f"   p=other   : {n_other}")
    print(f"   p=ghost   : {n_ghost}")
    if n_ghost == 0 and n_me != n_none:
        print("   ✅ #p NARROWS. The global_p_kind_index is live and filtering.")
        print("      -> an inbox subscription delivers only addressed events.")
    elif n_ghost == 0:
        print("   ✅ #p is applied (ghost yields nothing), though me == all here,")
        print("      which just means most events on this relay carry my p tag.")
    elif n_ghost == n_none:
        print("   ❌ #p is DECORATIVE on this build — accepted but not applied.")
        print("      -> inbox must filter client-side. Different design.")
    else:
        print("   ⚠ inconclusive — inspect the counts above.")
    print("=" * 70)


asyncio.run(main())
