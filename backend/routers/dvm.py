"""
NIP-90 DVM (Data Vantage Model) adapter — external job board.
Job requests: kinds 5000-5999
Job results:  kinds 6000-6999
Feedback:     kind 7000
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dvm", tags=["dvm"])

RELAY_URL = os.environ.get("NOSTR_RELAY_URL", "wss://omokoda.duckdns.org:3443/relay")


# ── Models ────────────────────────────────────────────────────────────────────

class DvmJobResult(BaseModel):
    job_event_id: str        # the event id of the originating kind 5xxx request
    output: str              # result content
    output_type: str = "text/plain"
    skill_tag: Optional[str] = None  # t tag value from original request

class DvmFeedback(BaseModel):
    job_event_id: str
    status: str              # "processing" | "success" | "error" | "payment-required"
    extra_info: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_event_id(event: dict) -> str:
    serialized = json.dumps(
        [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]],
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


async def _fetch_from_relay(kinds: list[int], limit: int = 50) -> list[dict]:
    """Fetch events of given kinds from relay via WebSocket."""
    try:
        import websockets
        import secrets as _secrets
        sub_id = _secrets.token_hex(8)
        results = []
        async with websockets.connect(RELAY_URL, open_timeout=8) as ws:
            # Handle AUTH challenge if present
            try:
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if first[0] == "AUTH":
                    # Skip AUTH for read-only fetch (relay may reject or allow)
                    pass
            except asyncio.TimeoutError:
                pass

            await ws.send(json.dumps(["REQ", sub_id, {"kinds": kinds, "limit": limit}]))
            while True:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if msg[0] == "EVENT" and msg[1] == sub_id:
                        results.append(msg[2])
                    elif msg[0] == "EOSE":
                        break
                    elif msg[0] in ("CLOSED", "NOTICE"):
                        break
                except asyncio.TimeoutError:
                    break
            await ws.send(json.dumps(["CLOSE", sub_id]))
        return results
    except Exception as e:
        logger.warning(f"DVM relay fetch failed: {e}")
        return []


async def _publish_event(event: dict) -> bool:
    """Publish a signed event to relay. Requires AGENT_NSEC env var."""
    try:
        import coincurve
        nsec_hex = os.environ.get("AGENT_NSEC", "")
        if not nsec_hex:
            logger.warning("DVM publish: AGENT_NSEC not set")
            return False

        privkey = coincurve.PrivateKey(bytes.fromhex(nsec_hex))
        pubkey_hex = privkey.public_key.format(compressed=True).hex()[2:]  # x-only

        event["pubkey"] = pubkey_hex
        event["id"] = _compute_event_id(event)
        sig = privkey.sign_schnorr(bytes.fromhex(event["id"]))
        event["sig"] = sig.hex()

        import websockets
        import secrets as _s
        async with websockets.connect(RELAY_URL, open_timeout=8) as ws:
            # Handle AUTH
            try:
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if first[0] == "AUTH":
                    challenge = first[1]
                    auth_ev = {
                        "pubkey": pubkey_hex,
                        "created_at": int(time.time()),
                        "kind": 22242,
                        "tags": [["relay", RELAY_URL], ["challenge", challenge]],
                        "content": "",
                    }
                    auth_ev["id"] = _compute_event_id(auth_ev)
                    auth_ev["sig"] = privkey.sign_schnorr(bytes.fromhex(auth_ev["id"])).hex()
                    await ws.send(json.dumps(["AUTH", auth_ev]))
                    await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                pass

            await ws.send(json.dumps(["EVENT", event]))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            return resp[0] == "OK" and resp[2] is True
    except Exception as e:
        logger.warning(f"DVM publish failed: {e}")
        return False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/jobs/open")
async def list_open_jobs(skill: Optional[str] = None, limit: int = 20):
    """
    Fetch open DVM job requests from relay (kinds 5000-5999).
    Optionally filter by skill/category t-tag.
    This is the endpoint EXTERNAL_JOB_BOARD_URL points to in task_router.py.
    """
    kinds = list(range(5000, 6000))
    events = await _fetch_from_relay(kinds, limit=limit)

    jobs = []
    for ev in events:
        tags = {t[0]: t[1] for t in ev.get("tags", []) if len(t) >= 2}
        skill_tag = tags.get("t", "")
        if skill and skill_tag != skill:
            continue
        jobs.append({
            "job_id": ev["id"],
            "kind": ev["kind"],
            "pubkey": ev["pubkey"],
            "created_at": ev["created_at"],
            "skill": skill_tag,
            "input": tags.get("i", ""),
            "output_type": tags.get("output", "text/plain"),
            "bid": tags.get("bid", "0"),
            "description": ev.get("content", ""),
        })

    return {"jobs": jobs, "count": len(jobs)}


@router.post("/jobs/{job_event_id}/result")
async def post_job_result(job_event_id: str, body: DvmJobResult):
    """
    Publish a NIP-90 job result (kind 6000-6999) back to relay.
    Kind = request_kind + 1000 (e.g. kind 5050 request -> kind 6050 result).
    """
    # Infer result kind from job_event_id prefix (default 6000 if unknown)
    result_kind = 6000  # fallback; proper impl would look up original event kind

    event = {
        "created_at": int(time.time()),
        "kind": result_kind,
        "tags": [
            ["e", job_event_id],
            ["p", ""],  # requester pubkey -- ideally fetched from original event
            ["request", json.dumps({"id": job_event_id})],
        ],
        "content": body.output,
    }
    if body.skill_tag:
        event["tags"].append(["t", body.skill_tag])

    ok = await _publish_event(event)
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to publish result to relay")
    return {"ok": True, "job_event_id": job_event_id, "result_kind": result_kind}


@router.post("/jobs/{job_event_id}/feedback")
async def post_job_feedback(job_event_id: str, body: DvmFeedback):
    """Publish NIP-90 feedback event (kind 7000)."""
    event = {
        "created_at": int(time.time()),
        "kind": 7000,
        "tags": [
            ["e", job_event_id],
            ["status", body.status, body.extra_info or ""],
        ],
        "content": body.extra_info or "",
    }
    ok = await _publish_event(event)
    return {"ok": ok, "job_event_id": job_event_id, "status": body.status}


@router.get("/health")
async def dvm_health():
    return {"status": "ok", "relay": RELAY_URL}
