"""Block clock endpoint — Bitcoin height + Koodu calendar for the UI.

Exposes `koodu_clock.snapshot()` so any surface (header chip, dashboards,
agents) reads the same time standard instead of `Date.now()`:

    {"clock": {height, now_est, uncertainty_s, wall_skew_s, next_block_eta_s...},
     "calendar": {btc_day, btc_domain, is_sabbath, halving_era...},
     "sabbath": false, "domain": "Score", "btc_day": 6770, "halving_era": 4}

Cached in-process: the underlying clock refreshes at most every 45s and the
Koodu snapshot spawns node, so a 15s cache keeps a busy UI from forking node on
every poll.
"""
import sys
import time

from fastapi import APIRouter

sys.path.insert(0, "/opt/ares")

router = APIRouter(prefix="/api", tags=["clock"])

_CACHE = {"at": 0.0, "payload": None}
_TTL = 15


@router.get("/clock")
def clock():
    now = time.time()
    if _CACHE["payload"] and now - _CACHE["at"] < _TTL:
        return _CACHE["payload"]
    try:
        import koodu_clock
        payload = koodu_clock.snapshot()
    except Exception as e:  # noqa: BLE001
        payload = {"ok": False, "error": str(e)[:200]}
    payload["served_at"] = int(now)
    payload["ok"] = bool((payload.get("clock") or {}).get("ok"))
    _CACHE.update(at=now, payload=payload)
    return payload


@router.get("/clock/height")
def height():
    """Cheap variant for polling badges: height + domain + whether the chain verified."""
    c = clock()
    if not c.get("ok"):
        return c
    cl = c["clock"]
    chn = cl.get("chain") or {}
    cont = chn.get("continuity") or {}
    return {"height": cl["height"], "domain": c.get("domain"),
            "sabbath": c.get("sabbath"), "now_est": cl["now_est"],
            "next_block_eta_s": cl["next_block_eta_s"],
            "verified": cl.get("verified", False),
            "headers_checked": chn.get("headers_checked"),
            "pow_ok": chn.get("pow_ok"),
            "continuity_ok": bool(cont.get("ok")),
            "verified_depth": (cont.get("verified_depth") or {}).get("count"),
            "verified_lowest": (cont.get("verified_depth") or {}).get("lowest"),
            "rooted": bool(chn.get("rooted")),
            "pin": cont.get("pin"),
            "anchor_height": cont.get("anchor_height"),
            "anchor_seeded": bool(cont.get("seeded") or cont.get("bootstrapped")),
            "anchor_sources": (cont.get("seeded") or {}).get("sources"),
            "gap_walked": cont.get("walked"),
            "skew_s": cl.get("skew_s"),
            "halving_era": (c.get("halving_era") if c.get("halving_era") is not None
                            else cl.get("halving_era")),
            "uncertainty_s": cl.get("uncertainty_s"),
            "degraded": cl.get("degraded")}


@router.get("/clock/now")
def clock_now(require: str = ""):
    """The clock an agent asks. Same object as `okf_now.py`, so a human tool and
    an agent tool can never disagree about what time it is.

    Exit-code semantics are expressed as HTTP:
      200 verified + rooted · 424 verified but unrooted · 503 unverified
    `?require=rooted` turns the 424 into a 424 with `ok:false`, so an agent that
    must not stamp an unrooted time can fail closed in one call.
    """
    from fastapi import Response
    import json as _json
    import subprocess

    try:
        r = subprocess.run(["/opt/ares/venv/bin/python", "/opt/ares/okf_now.py"],
                           capture_output=True, text=True, timeout=90)
        payload = _json.loads(r.stdout)
        rc = r.returncode
    except Exception as e:  # noqa: BLE001
        payload, rc = {"ok": False, "verdict": "unverified", "error": str(e)[:200]}, 1

    if require == "rooted" and not (payload.get("root") or {}).get("rooted"):
        payload["required"] = "rooted"
        payload["ok"] = False
        rc = 3
    code = {0: 200, 3: 424}.get(rc, 503)
    resp = Response(content=_json.dumps(payload),
                    media_type="application/json", status_code=code)
    resp.headers["X-BTC-Height"] = str((payload.get("block") or {}).get("height") or "")
    resp.headers["X-BTC-Verified"] = str(bool(payload.get("ok"))).lower()
    resp.headers["Cache-Control"] = "max-age=15"
    return resp
