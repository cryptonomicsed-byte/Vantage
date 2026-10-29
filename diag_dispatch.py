"""Trace WHICH stage of _dispatch_chat fails for a given agent.

_dispatch_chat tries, in order:
  1. agent_row['cognition_url']  -> POST -> {"reply": ...}
  2. get_active_provider_for_chat(agent_id) -> try_active_provider
  3. _try_omniroute(agent_row, text)  -> built-in LLM
  4. parse_intent(text) -> regex -> "unknown"  <-- what we observed

Reaching 4 means 1, 2 and 3 all failed. This reports each one.
"""
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, "/opt/ares/Vantage")

AGENT = int(sys.argv[1]) if len(sys.argv) > 1 else 18
TEXT = sys.argv[2] if len(sys.argv) > 2 else "hello, what can you do?"

DB = "/opt/ares/Vantage/data/vantage.db"


def row() -> dict:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM agents WHERE id = ?", (AGENT,)).fetchone()
    c.close()
    return dict(r) if r else {}


async def main():
    r = row()
    print("=" * 76)
    print("  DISPATCH TRACE -- agent %d" % AGENT)
    print("  text: %r" % TEXT)
    print("=" * 76)

    if not r:
        print("  NO SUCH AGENT")
        return

    print("\n  [agent row, interesting columns]")
    for k in sorted(r.keys()):
        v = r[k]
        if v in (None, "", 0):
            continue
        s = str(v)
        if any(t in k.lower() for t in ("token", "key", "secret")):
            s = "<set len=%d>" % len(s)
        print("    %-28s = %s" % (k, s[:70]))

    # ── stage 1: cognition_url ──
    print("\n  [stage 1] cognition_url")
    cu = r.get("cognition_url")
    if not cu:
        print("    not configured -> skipped")
    else:
        print("    url: %s" % cu)
        try:
            import httpx
            hdrs = {}
            tok = r.get("cognition_auth_token")
            if tok:
                hdrs["Authorization"] = "Bearer %s" % tok
            payload = {"agent_name": r.get("name", ""), "text": TEXT, "human_id": "diag"}
            if r.get("omokoda_agent_id"):
                payload["agent_id"] = r["omokoda_agent_id"]
                payload["agent_key"] = r.get("omokoda_agent_key")
            async with httpx.AsyncClient(timeout=30.0) as cl:
                resp = await cl.post(cu, json=payload, headers=hdrs)
            print("    HTTP %s" % resp.status_code)
            print("    body: %s" % resp.text[:200])
        except Exception as e:
            print("    FAILED: %s: %s" % (type(e).__name__, e))

    # ── stage 2: provider credential ──
    print("\n  [stage 2] owner provider credential")
    try:
        from backend.provider_credentials import get_active_provider_for_chat
        cred = await get_active_provider_for_chat(AGENT)
        print("    credential: %s" % ("<set>" if cred else "none configured -> skipped"))
        if cred:
            from backend.llm_provider_client import try_active_provider
            out = await try_active_provider(r, TEXT, cred)
            print("    reply: %r" % (str(out)[:150] if out else None))
    except Exception as e:
        print("    FAILED: %s: %s" % (type(e).__name__, e))

    # ── stage 3: omniroute ──
    print("\n  [stage 3] _try_omniroute")
    try:
        from backend.routers.copilot import _try_omniroute
        out = await _try_omniroute(r, TEXT)
        print("    reply: %r" % (str(out)[:150] if out else None))
    except Exception as e:
        import traceback
        print("    FAILED: %s: %s" % (type(e).__name__, e))
        traceback.print_exc()

    # ── env the stages depend on ──
    print("\n  [env]")
    for k in ("VANTAGE_OMNIROUTE_URL", "VANTAGE_OMNIROUTE_API_KEY"):
        v = os.environ.get(k)
        print("    %-26s = %s" % (k, "<set len=%d>" % len(v) if v else "MISSING"))

    # ── final: what the bridge would see ──
    print("\n  [final] _dispatch_chat result")
    try:
        from backend.routers.copilot import _dispatch_chat
        res = await _dispatch_chat(dict(r), TEXT, human_id="buzz:diag")
        data = res.get("data", {})
        print("    action = %s" % res.get("action"))
        print("    target = %s" % res.get("target"))
        print("    data   = %s" % str(data)[:200])
        print("\n    bridge would publish: %s"
              % (data.get("reply") or "[%s] %s" % (res.get("action"), data)))
    except Exception as e:
        print("    FAILED: %s: %s" % (type(e).__name__, e))
    print("=" * 76)


asyncio.run(main())
