"""Standardized cover art for every agent upload -- the fix for
inconsistent/missing thumbnails across Cinema/Audio/podcasts (podcasts in
particular were always published with thumbnail_url="", a blank tile,
while Audio enforces real cover art per track).

Real image generation (DALL-E/Stable-Diffusion-style) isn't wired -- no
free/local image-gen backend has been confirmed reachable from this host
the way OmniRoute (text) and edge-tts (voice) are. Being honest about that
limitation: instead of faking a generator, this enforces a real, visible
standard -- a valid uploader-provided cover is used as-is; anything
missing or malformed is deterministically assigned one of 5 real
in-house-designed posters (frontend/public/covers/default-{1..5}.svg),
picked by a stable hash of the content's own identity so the same
agent/category always lands on the same poster rather than flickering
between covers on every reload.
"""
import hashlib

DEFAULT_COVERS = [
    "/covers/default-1.svg",  # Nebula Pulse -- general / flagship
    "/covers/default-2.svg",  # Circuit Pulse -- AI / tech
    "/covers/default-3.svg",  # Ledger Peak -- crypto / markets
    "/covers/default-4.svg",  # Degen Blaze -- hype / degen
    "/covers/default-5.svg",  # Signal Grid -- neutral / everything else
]

CATEGORY_COVER = {
    "agent.tv": DEFAULT_COVERS[0],
    "ai news": DEFAULT_COVERS[1],
    "crypto tier one": DEFAULT_COVERS[2],
    "degen frequency": DEFAULT_COVERS[3],
}


def is_valid_cover(url: str | None) -> bool:
    """Real format check, not just "is it non-empty" -- must look like an
    actual reachable image reference (absolute http(s) URL or a path into
    one of our own media mounts), with a sane length. Anything else (a
    bare filename, a data: blob that's empty, garbage text) doesn't count
    as "the uploader provided a real cover"."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if len(url) < 5 or len(url) > 2000:
        return False
    return url.startswith(("http://", "https://", "/media/", "/covers/"))


def resolve_cover(thumbnail_url: str | None, seed: str, category: str = "") -> str:
    """The single standardization point -- call this wherever a broadcast's
    thumbnail is set. `seed` should be something stable about the content
    (e.g. f"{agent_id}:{category}") so repeated calls for the same
    agent/category land on the same default poster instead of jittering."""
    if is_valid_cover(thumbnail_url):
        return thumbnail_url.strip()  # type: ignore[union-attr]
    cat_key = (category or "").strip().lower()
    if cat_key in CATEGORY_COVER:
        return CATEGORY_COVER[cat_key]
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(DEFAULT_COVERS)
    return DEFAULT_COVERS[idx]
