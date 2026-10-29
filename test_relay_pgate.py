"""TEST 0 — the relay gate experiment.

Question: can ONE authenticated connection subscribe to #p for a pubkey it
does not itself hold?

  Model A (instance custody)  -> subscribe(#p=[other agents]) is ACCEPTED
  Model B (agent identity)    -> REJECTED; each agent must auth for itself

Run on the Vantage box with /opt/ares/venv/bin/python3, from /opt/ares/Vantage.
Publishes nothing. Only reads.
"""
import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, "/opt/ares/Vantage")

# Seeds are encrypted with VANTAGE_SEED_MASTER_KEY, which lives in the
# systemd unit's environment -- not in the shell. Read it off the running
# process so this probe sees exactly what the app sees.
_pid = subprocess.run(["systemctl", "show", "vantage", "-p", "MainPID", "--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for _entry in open(f"/proc/{_pid}/environ", "rb").read().decode(errors="replace").split("\0"):
        if "=" in _entry:
            _k, _v = _entry.split("=", 1)
            os.environ.setdefault(_k, _v)

from backend.buzz_client import BuzzSession, build_event  # noqa: E402
from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex  # noqa: E402
from backend.buzz_registration import RELAY_WS_URL  # noqa: E402

SUBSCRIBER_AGENT = 5     # Hermes-Ares
TARGET_AGENT = 443       # probe-register-e2e  (a DIFFERENT pubkey)


async def probe(label: str, filters: list) -> None:
    pk = await derive_buzz_keypair(SUBSCRIBER_AGENT)
    sess = BuzzSession(RELAY_WS_URL, pk)
    await sess.connect()
    await sess.authenticate()
    sub = await sess.subscribe(filters)
    print(f"\n── {label}")
    print(f"   REQ filters: {json.dumps(filters)}")
    # recv_until_eose returns on EOSE (= accepted) and raises RuntimeError on
    # CLOSED (= rejected). Its docstring says it exists precisely to surface
    # this gate, so it is the right instrument for the question.
    try:
        events = await asyncio.wait_for(sess.recv_until_eose(sub, max_events=5), timeout=20)
        print(f"   ✅ EOSE — ACCEPTED. {len(events)} existing event(s) matched.")
    except asyncio.TimeoutError:
        print("   ⚠ no EOSE and no CLOSED within 20s (relay silent)")
    except RuntimeError as e:
        print(f"   ❌ {e}")
    except Exception as e:
        print(f"   ⚠ {type(e).__name__}: {e}")
    finally:
        await sess.close()


async def main() -> None:
    pk_self = await derive_buzz_keypair(SUBSCRIBER_AGENT)
    pk_other = await derive_buzz_keypair(TARGET_AGENT)
    me = public_key_xonly_hex(pk_self)
    other = public_key_xonly_hex(pk_other)

    print("=" * 68)
    print(f"  subscriber : agent {SUBSCRIBER_AGENT}  {me[:16]}…")
    print(f"  target     : agent {TARGET_AGENT}  {other[:16]}…")
    print(f"  relay      : {RELAY_WS_URL}")
    print("=" * 68)

    # CONTROL: subscribe for our own pubkey. Should always be accepted.
    await probe("CONTROL — #p = self", [{"kinds": [9, 1], "#p": [me]}])

    # THE EXPERIMENT: subscribe for a DIFFERENT agent's pubkey.
    await probe("EXPERIMENT — #p = another agent", [{"kinds": [9, 1], "#p": [other]}])

    # MULTI: both at once — what the instance-inbox design needs.
    await probe("EXPERIMENT — #p = self + other (the real design)",
                [{"kinds": [9, 1], "#p": [me, other]}])

    # Is the gate about the #p tag, or about kinds? Drop #p entirely.
    await probe("CONTROL — no #p, kinds only", [{"kinds": [9, 1]}])

    print("\n" + "=" * 68)
    print("  READ THE RESULT:")
    print("   CONTROL accepted + EXPERIMENT rejected -> Model B (per-agent auth)")
    print("   CONTROL accepted + EXPERIMENT accepted -> Model A (instance custody)")
    print("=" * 68)


asyncio.run(main())
