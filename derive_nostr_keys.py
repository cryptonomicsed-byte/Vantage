"""Write agent 5's Nostr secret to a 0600 file for nak, and print public facts only."""
import asyncio, os, subprocess, sys
sys.path.insert(0, "/opt/ares/Vantage")
_pid = subprocess.run(["systemctl","show","vantage","-p","MainPID","--value"],
                      capture_output=True, text=True).stdout.strip()
if _pid.isdigit():
    for e in open(f"/proc/{_pid}/environ","rb").read().decode(errors="replace").split("\0"):
        if "=" in e:
            k,v = e.split("=",1); os.environ.setdefault(k,v)

from backend.buzz_identity import derive_buzz_keypair, public_key_xonly_hex

async def main():
    for agent_id, out in ((5, "/opt/ares/coordinator/agent5.sec"),
                          (443, "/opt/ares/coordinator/agent443.sec")):
        pk = await derive_buzz_keypair(agent_id)
        hexsec = pk.secret.hex() if hasattr(pk, "secret") else pk.private_key.hex()
        with open(out, "w") as fh:
            fh.write(hexsec)
        os.chmod(out, 0o600)
        pub = public_key_xonly_hex(pk)
        print(f"agent {agent_id}: pub={pub}")
        print(f"           sec -> {out} (0600, {len(hexsec)} hex chars)")

asyncio.run(main())
