"""Franken-stream -- proxy into franken-stream's own web API (search +
embed resolution across streaming-mirror providers), a separate Python
service (ares-frankenstream.service, localhost:3034) rather than ported
into Vantage's own backend, same pattern as agenttv_proxy.py.

Disclosed, not hidden: results come from unlicensed streaming-mirror
sites whose availability rots quickly -- some results will be dead links,
that's inherent to this class of source, not a bug in this proxy."""
import httpx
from fastapi import APIRouter, HTTPException, Request

from ..deps import _parse_body

router = APIRouter(prefix="/api/cinema/livetv", tags=["cinema"])

FRANKENSTREAM_BASE = "http://localhost:3034"


async def _forward(method: str, path: str, **kwargs) -> dict:
    try:
        # Provider fetches now route through Tor (see franken-stream's own
        # ares-frankenstream.service config) -- genuinely slower than a
        # direct connection, confirmed live (~70s for a full multi-provider
        # search vs ~22s before). 120s gives real margin over that.
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.request(method, f"{FRANKENSTREAM_BASE}{path}", **kwargs)
            if r.status_code >= 400:
                detail = r.text
                try:
                    detail = r.json().get("detail", detail)
                except Exception:
                    pass
                raise HTTPException(r.status_code, detail)
            return r.json()
    except httpx.RequestError as e:
        raise HTTPException(502, f"franken-stream service unreachable: {e}")


@router.post("/search")
async def search(request: Request):
    body = await _parse_body(request)
    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(422, "query is required")
    return await _forward("POST", "/api/search", json={"query": query})


@router.post("/embed")
async def resolve_embed(request: Request):
    body = await _parse_body(request)
    url = body.get("url")
    if not url:
        raise HTTPException(422, "url is required")
    return await _forward("POST", "/api/embed", json={"url": url, "base_url": body.get("base_url")})
