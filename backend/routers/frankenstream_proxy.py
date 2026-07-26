"""Franken-stream -- proxy into franken-stream's own web API (search +
embed resolution across streaming-mirror providers), a separate Python
service (ares-frankenstream.service, localhost:3034) rather than ported
into Vantage's own backend, same pattern as agenttv_proxy.py.

Disclosed, not hidden: results come from unlicensed streaming-mirror
sites whose availability rots quickly -- some results will be dead links,
that's inherent to this class of source, not a bug in this proxy."""
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..deps import _parse_body

router = APIRouter(prefix="/api/cinema/livetv", tags=["cinema"])

# Audio "Stream" tab -- same franken-stream backend service, separate
# prefix/router since this is a distinct Vantage surface (Audio, not
# Cinema). Legal-first sources only: iTunes metadata/previews, podcast
# RSS, SoundCloud oEmbed, YouTube (gated on a key). See
# franken_stream/audio_sources.py for the full rationale.
audio_router = APIRouter(prefix="/api/audio/stream", tags=["audio"])

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


@router.get("/trending")
async def trending(window: str = "week", media_type: str = "all"):
    return await _forward("GET", f"/api/trending?window={window}&media_type={media_type}")


@router.get("/now-playing")
async def now_playing():
    return await _forward("GET", "/api/now-playing")


@router.get("/upcoming")
async def upcoming():
    return await _forward("GET", "/api/upcoming")


@router.get("/genres")
async def genres(media_type: str = "movie"):
    return await _forward("GET", f"/api/genres?media_type={media_type}")


@router.get("/discover")
async def discover(genre_id: int, media_type: str = "movie"):
    return await _forward("GET", f"/api/discover?genre_id={genre_id}&media_type={media_type}")


@router.get("/live/countries")
async def live_countries():
    return await _forward("GET", "/api/live/countries")


@router.get("/live/channels/{country_code}")
async def live_channels(country_code: str):
    return await _forward("GET", f"/api/live/channels/{country_code}")


@audio_router.get("/search")
async def audio_search(term: str, media: str = "music", entity: str | None = None):
    params = {"term": term, "media": media}
    if entity:
        params["entity"] = entity
    return await _forward("GET", "/api/audio/search", params=params)


@audio_router.get("/album/tracks")
async def audio_album_tracks(collection_id: int):
    return await _forward("GET", "/api/audio/album/tracks", params={"collection_id": collection_id})


@audio_router.get("/artist/albums")
async def audio_artist_albums(artist_id: int):
    return await _forward("GET", "/api/audio/artist/albums", params={"artist_id": artist_id})


@audio_router.get("/mixtapes")
async def audio_mixtapes(artist: str):
    return await _forward("GET", "/api/audio/mixtapes", params={"artist": artist})


@audio_router.get("/mixtape/tracks")
async def audio_mixtape_tracks(identifier: str):
    result = await _forward("GET", "/api/audio/mixtape/tracks", params={"identifier": identifier})
    # Same rewrite as resolve-full-track: franken-stream returns relative
    # URLs pointing at ITS OWN /api/audio/archive/stream -- point them at
    # Vantage's own passthrough instead.
    for track in result.get("tracks", []):
        u = track.get("url", "")
        if u.startswith("/api/audio/archive/stream"):
            track["url"] = u.replace("/api/audio/archive/stream", "/api/audio/stream/archive/stream", 1)
    return result


@audio_router.get("/podcast/episodes")
async def audio_podcast_episodes(feed_url: str):
    return await _forward("GET", "/api/audio/podcast/episodes", params={"feed_url": feed_url})


@audio_router.get("/soundcloud/embed")
async def audio_soundcloud_embed(url: str):
    return await _forward("GET", "/api/audio/soundcloud/embed", params={"url": url})


@audio_router.get("/youtube/search")
async def audio_youtube_search(term: str):
    return await _forward("GET", "/api/audio/youtube/search", params={"term": term})


@audio_router.get("/musify/search")
async def audio_musify_search(term: str):
    """Full-length music via musify.club -- DISCLOSED, NOT HIDDEN:
    unlicensed commercial-music mirror, meaningfully higher legal risk
    than the movie-embed providers. Owner-authorized after being told
    the legal-first alternatives exist."""
    return await _forward("GET", "/api/audio/musify/search", params={"term": term})


@audio_router.get("/musify/resolve")
async def audio_musify_resolve(track_url: str):
    return await _forward("GET", "/api/audio/musify/resolve", params={"track_url": track_url})


@audio_router.get("/search/grouped")
async def audio_search_grouped(term: str):
    return await _forward("GET", "/api/audio/search/grouped", params={"term": term})


@audio_router.get("/charts")
async def audio_charts(genre_id: int | None = None, limit: int = 25):
    params: dict = {"limit": limit}
    if genre_id is not None:
        params["genre_id"] = genre_id
    return await _forward("GET", "/api/audio/charts", params=params)


@audio_router.get("/genres")
async def audio_genres():
    return await _forward("GET", "/api/audio/genres")


@audio_router.get("/resolve-full-track")
async def audio_resolve_full_track(title: str, artist: str = ""):
    result = await _forward("GET", "/api/audio/resolve-full-track", params={"title": title, "artist": artist})
    # franken-stream returns a relative stream_url pointing at ITS OWN
    # /api/audio/proxy-stream -- rewrite it to Vantage's own passthrough
    # route (below) so the browser only ever talks to Vantage's origin,
    # not franken-stream's directly (which isn't publicly exposed anyway).
    if result.get("stream_url", "").startswith("/api/audio/proxy-stream"):
        from urllib.parse import quote
        result["stream_url"] = f"/api/audio/stream/proxy-stream?title={quote(title)}&artist={quote(artist)}"
    return result


@audio_router.get("/jamendo/search")
async def audio_jamendo_search(term: str):
    return await _forward("GET", "/api/audio/jamendo/search", params={"term": term})


async def _stream_from_frankenstream(request: Request, path: str, params: dict) -> StreamingResponse:
    """Shared raw byte passthrough (not the generic JSON _forward helper)
    for both /proxy-stream (musify) and /archive/stream (archive.org
    mixtapes) -- franken-stream's own endpoints already do the real fix
    (server-to-server fetch avoids musify's Origin-header hotlink check
    and archive.org's inconsistent per-format CORS); this just relays
    those bytes one hop further, forwarding Range requests both ways."""
    headers = {}
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    upstream_req = client.build_request("GET", f"{FRANKENSTREAM_BASE}{path}", params=params, headers=headers)
    upstream_resp = await client.send(upstream_req, stream=True)

    if upstream_resp.status_code >= 400:
        detail = (await upstream_resp.aread()).decode(errors="replace")
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(upstream_resp.status_code, detail)

    async def body_iterator():
        try:
            async for chunk in upstream_resp.aiter_bytes(65536):
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    passthrough_headers = {}
    for h in ("content-type", "content-length", "content-range", "accept-ranges"):
        if h in upstream_resp.headers:
            passthrough_headers[h] = upstream_resp.headers[h]

    return StreamingResponse(
        body_iterator(),
        status_code=upstream_resp.status_code,
        headers=passthrough_headers,
        media_type=upstream_resp.headers.get("content-type", "audio/mpeg"),
    )


@audio_router.get("/proxy-stream")
async def audio_proxy_stream(request: Request, title: str, artist: str = ""):
    return await _stream_from_frankenstream(request, "/api/audio/proxy-stream", {"title": title, "artist": artist})


@audio_router.get("/archive/stream")
async def audio_archive_stream(request: Request, url: str):
    return await _stream_from_frankenstream(request, "/api/audio/archive/stream", {"url": url})
