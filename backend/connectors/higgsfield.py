"""Higgsfield connector — text-to-video generation for video mode.

API: POST https://api.higgsfield.ai/v1/video/generate (job), poll
/v1/video/{id} until complete. Token comes from HIGGSFIELD_API_KEY. When the
key is absent the connector reports configured=False and the lounge falls
back to the native AgentTV pipeline — a missing optional backend must never
break a mode.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.higgsfield.ai"
_KEY = os.environ.get("HIGGSFIELD_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_KEY)


async def generate_text_to_video(prompt: str, duration_sec: int = 5) -> dict:
    """Submit a generation job and poll it to completion (bounded)."""
    if not is_configured():
        return {"configured": False, "note": "HIGGSFIELD_API_KEY not set — native AgentTV pipeline remains the default"}
    headers = {"Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{BASE_URL}/v1/video/generate",
                json={"prompt": prompt, "duration": duration_sec},
                headers=headers,
            )
        if resp.status_code not in (200, 201):
            return {"configured": True, "ok": False, "error": f"higgsfield HTTP {resp.status_code}: {resp.text[:200]}"}
        job = resp.json()
        job_id = job.get("id") or job.get("job_id")
        if not job_id:
            return {"configured": True, "ok": False, "error": "no job id in higgsfield response"}
        # Poll up to ~60s for completion; video jobs are slow.
        for _ in range(20):
            async with httpx.AsyncClient(timeout=10) as client:
                status = await client.get(f"{BASE_URL}/v1/video/{job_id}", headers=headers)
            if status.status_code != 200:
                continue
            data = status.json()
            if data.get("status") in ("completed", "succeeded"):
                return {"configured": True, "ok": True, "job_id": job_id,
                        "video_url": data.get("video_url") or data.get("url")}
            if data.get("status") in ("failed", "error"):
                return {"configured": True, "ok": False, "job_id": job_id,
                        "error": str(data.get("error") or "generation failed")}
            import asyncio
            await asyncio.sleep(3)
        return {"configured": True, "ok": False, "job_id": job_id,
                "error": "timed out waiting for generation"}
    except Exception as exc:
        logger.warning("higgsfield connector failed: %s", exc)
        return {"configured": True, "ok": False, "error": f"unreachable: {exc}"}
