"""Store the working Gemini key as agent 18's active chat provider, then prove
_dispatch_chat returns a real LLM reply instead of falling through to the
regex parser.

Context: agent 18's chat fell back to `unknown` because ALL three LLM paths
failed -- no cognition_url, no provider credential, and OmniRoute 503 (its
DeepSeek account key is invalid, its Anthropic account has no credit).
Gemini is the only live provider on this box.

Usage:  GEMINI_KEY=... python store_gemini_cred.py [agent_id]
"""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/ares/Vantage")

AGENT = int(sys.argv[1]) if len(sys.argv) > 1 else 18
KEY = os.environ.get("GEMINI_KEY", "").strip()
PROVIDER = "google"


async def main():
    if not KEY:
        print("  GEMINI_KEY not set in the environment -- refusing to store an empty key")
        sys.exit(1)

    from backend.provider_credentials import (
        save_credential, set_active_provider, get_active_provider_for_chat, list_credentials,
    )

    print("=" * 74)
    print("  STORE GEMINI CREDENTIAL -- agent %d" % AGENT)
    print("=" * 74)

    saved = await save_credential(AGENT, PROVIDER, KEY)
    print("  saved credential: %s" % str(saved)[:200])

    act = await set_active_provider(AGENT, PROVIDER)
    print("  set active:       %s" % str(act)[:200])

    print("\n  credentials now on agent %d:" % AGENT)
    for c in await list_credentials(AGENT):
        print("    %s" % str(c)[:170])

    cred = await get_active_provider_for_chat(AGENT)
    print("\n  get_active_provider_for_chat -> %s"
          % ("<set> provider=%s model=%s base=%s" % (
              cred.get("provider_id"), cred.get("model"), cred.get("base_url"))
             if cred else "NONE  <-- chat will still fall back!"))

    # ── the real test ──
    print("\n" + "=" * 74)
    print("  _dispatch_chat with a plain conversational message")
    print("=" * 74)
    import sqlite3
    c = sqlite3.connect("/opt/ares/Vantage/data/vantage.db")
    c.row_factory = sqlite3.Row
    row = dict(c.execute("SELECT * FROM agents WHERE id = ?", (AGENT,)).fetchone())
    c.close()

    from backend.routers.copilot import _dispatch_chat
    res = await _dispatch_chat(row, "hello, what can you do?", human_id="buzz:diag")
    data = res.get("data") or {}
    print("  action = %s" % res.get("action"))
    print("  data   = %s" % str(data)[:400])
    reply = data.get("reply")
    print("\n  REPLY: %s" % (reply if reply else "<none>"))
    print("\n  bridge would publish: %s" % (reply or "[%s] %s" % (res.get("action"), data)))
    print("=" * 74)

    if reply:
        print("  RESULT: real LLM reply -- chat path restored.")
    else:
        print("  RESULT: still no reply -- check base_url/model and provider logs.")


asyncio.run(main())
