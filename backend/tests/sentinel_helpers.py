"""Test helpers: named Sentinel operators and a cleared live switch."""
import hashlib
import secrets

from backend.config import settings
from backend.db import get_db

ADMIN = {"X-Admin-Key": settings.ADMIN_KEY}   # whatever key this run configured (CI sets its own)


async def operator(client, role="operator"):
    token = secrets.token_hex(16)
    async with get_db() as db:
        cur = await db.execute("INSERT INTO humans (email, password_hash, display_name) VALUES (?,?,?)",
                               (f"{token}@t.test", "x", f"H{token[:6]}"))
        hid = cur.lastrowid
        await db.execute("INSERT INTO human_sessions (human_id, token_hash, expires_at) "
                         "VALUES (?,?,datetime('now','+1 day'))", (hid, hashlib.sha256(token.encode()).hexdigest()))
        await db.commit()
    r = await client.post("/api/admin/sentinel/operators", headers=ADMIN, json={"human_id": hid, "role": role})
    assert r.status_code == 200, r.text
    return {"id": hid, "h": {"X-Human-Session": token}}


async def switch_on_via_signoff(client, monkeypatch):
    """Open the ceiling, file a card as one operator, approve as another, with
    the gate cleared — the only way the instance switch turns on."""
    from backend import esu_gate
    from backend import trading_mode as tm

    async def ok(op):
        return esu_gate.GatekeeperResult(approved=True)
    monkeypatch.setattr(esu_gate, "evaluate", ok)
    monkeypatch.setenv(tm.GLOBAL_LIVE_ENV, "1")
    req, appr = await operator(client), await operator(client, "security")
    r = await client.put("/api/trading/live-mode", headers=req["h"], json={"enabled": True, "note": "test"})
    assert r.status_code == 202, r.text
    r = await client.post(f"/api/sentinel/signoffs/{r.json()['signoff']['id']}/approve", headers=appr["h"])
    assert r.json()["status"] == "approved", r.text
    tm.invalidate_cache()
    return req, appr
