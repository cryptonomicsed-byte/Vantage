"""Public passthrough to the Omo-Koda2 kernel's /v1/cognition webhook.

Real infra gap found by Omo-Koda2 while wiring Buzz's CallWebhook workflow
action to the kernel (2026-07-29, see the shared vault note
omokoda-workflow-webhook-e2e-findings-2026-07-29.md): Buzz's workflow
engine has a real, hardcoded, non-configurable SSRF guard
(buzz_core::network::is_private_ip) that unconditionally rejects
loopback/private/reserved IPs -- correct, working security behavior, not
a bug to work around by weakening it. `http://localhost:7777/v1/cognition`
can never pass that check from Buzz's side.

The fix has to live here instead: Vantage's backend already has a real
public HTTPS front door (settings.PUBLIC_URL, e.g.
https://omokoda.duckdns.org) and already proxies other local-only
services the same way (see routers/frankenstream_proxy.py). This route
does the same thing for the kernel -- a workflow's CallWebhook step points
at https://<this-instance>/api/omokoda-cognition-proxy instead of
localhost, which resolves to a genuine public IP and passes Buzz's SSRF
check for real, then this route forwards the request to the actual
kernel on the same host.

Security: NOT an open passthrough -- requires the caller to present the
same bearer token the kernel itself expects (settings.OMOKODA_COGNITION_TOKEN),
checked here with a timing-safe comparison before forwarding. This
preserves the same "you need the real token" property a direct localhost
call would have had; it does not create a new unauthenticated path to the
kernel just because it's now reachable from the public internet.
"""
import hmac
import logging

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["platform"])


@router.post("/omokoda-cognition-proxy", operation_id="omokoda_cognition_proxy")
async def omokoda_cognition_proxy(request: Request, authorization: str = Header(default="")):
    if not settings.OMOKODA_URL or not settings.OMOKODA_COGNITION_TOKEN:
        raise HTTPException(503, "Omo-Koda2 kernel not configured on this instance")

    provided = authorization.removeprefix("Bearer ").strip()
    expected = settings.OMOKODA_COGNITION_TOKEN
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(401, "Invalid or missing bearer token")

    body = await request.body()
    kernel_url = f"{settings.OMOKODA_URL.rstrip('/')}/v1/cognition"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                kernel_url,
                content=body,
                headers={
                    "Authorization": f"Bearer {expected}",
                    "Content-Type": request.headers.get("content-type", "application/json"),
                },
            )
    except httpx.RequestError as e:
        logger.warning("omokoda-cognition-proxy: kernel unreachable: %s", e)
        raise HTTPException(502, f"Kernel unreachable: {e}")

    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text}
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, payload if isinstance(payload, (str, dict)) else str(payload))
    return payload
