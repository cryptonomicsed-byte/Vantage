"""YouTube video-description extraction — no API key required.

Deliberately separate from any trigger/watcher mechanism (cron, webhook,
manual paste) so that whichever trigger calls `extract_github_urls_from_video`
today (a manual paste endpoint) can be swapped later for an actual
YouTube Data API v3 channel-watcher cron without touching this module at
all — the watcher only needs to supply a video URL/ID, same as the manual
path does now.

Two independent ways to get a video's description, no API key either way:
  1. yt-dlp (if installed) — most robust, handles YouTube's page-format
     changes for us. Used automatically when the binary is on PATH.
  2. Plain HTTP GET of the public watch page + regex extraction of the
     `shortDescription` field out of the embedded `ytInitialPlayerResponse`
     JSON blob every watch page ships. No auth, no quota, just parsing a
     public page — exactly what a browser does before any JS runs.

If yt-dlp is present it's tried first (more robust); the HTTP fallback
always runs if yt-dlp is absent or fails, so this never hard-depends on an
optional binary.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
# youtube.com/watch?list=PLAYLIST&v=ID -- the `v=` param isn't always first
# in the query string (a link copied from inside a playlist puts `list=`
# before it). The pattern above requires `watch?v=` literally at the start
# of the query, so it never matched this real, common URL shape; this is a
# fallback for `v=` appearing anywhere in a watch-page query string.
_VIDEO_ID_QUERY_RE = re.compile(r"youtube\.com/watch\?(?:[^#\s]*&)?v=([A-Za-z0-9_-]{11})")

# github.com/{owner}/{repo} — owner/repo segments are the GitHub-legal
# charset (alnum, hyphen, underscore, dot for repo names). Trailing
# punctuation a human typed right after a URL (. , ) ] etc) is stripped
# separately below rather than excluded here, so "repo)." at a sentence's
# end doesn't truncate a repo name that legitimately ends the same way.
_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9][A-Za-z0-9_.-]*)"
)

# Any repo host (github/gitlab/gitea/bitbucket/self-hosted) — used by the
# frankenstein harvester so descriptions pointing at non-GitHub repos are
# not lost. Host must look like a real hostname (dots allowed, no scheme
# artifacts); owner/repo use the same legal charset as GitHub's.
_REPO_URL_RE = re.compile(
    r"https?://([a-z0-9.-]+\.(?:com|org|io|dev|net|cloud|app))/([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9][A-Za-z0-9_.-]*?)(?:\.git)?(?:/|$|\s|[.,;:!?)\]}>\"'])",
    re.IGNORECASE,
)

# Hosts that look like repo hosts but never point at a forgeable repo.
_REPO_SKIP_HOSTS = {
    "www.youtube.com", "youtu.be", "youtube.com", "m.youtube.com",
    "www.google.com", "raw.githubusercontent.com",
}

_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?)\]}'\"`]+$")


@dataclass
class VideoExtraction:
    video_id: str
    source_url: str
    title: Optional[str] = None
    description: str = ""
    method: str = ""  # "yt-dlp" | "http" — which path actually produced the description
    github_urls: list[str] = field(default_factory=list)
    repo_urls: list[str] = field(default_factory=list)
    error: Optional[str] = None


def extract_video_id(url_or_id: str) -> Optional[str]:
    """Accepts a full YouTube URL (watch/shorts/embed/youtu.be) or a bare
    11-char video ID and returns the video ID, or None if unrecognized."""
    s = url_or_id.strip()
    m = _VIDEO_ID_RE.search(s)
    if m:
        return m.group(1)
    m = _VIDEO_ID_QUERY_RE.search(s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    return None


def extract_github_urls(text: str) -> list[str]:
    """Pull github.com/owner/repo links out of free text, deduped and
    normalized (scheme forced to https, trailing punctuation stripped,
    no trailing slash). Order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _GITHUB_URL_RE.finditer(text):
        owner, repo = m.group(1), m.group(2)
        repo = _TRAILING_PUNCT_RE.sub("", repo)
        if not repo or not owner:
            continue
        # A bare "github.com/owner/repo.git" style link's repo segment
        # legitimately ends in a real `.git` — only strip the punctuation
        # regex above (trailing sentence punctuation), never the literal
        # ".git" suffix itself.
        url = f"https://github.com/{owner}/{repo}"
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def extract_repo_urls(text: str) -> list[str]:
    """Pull owner/repo links from ANY host (github/gitlab/gitea/bitbucket/
    self-hosted) out of free text — the frankenstein-harvest view. Same
    normalization as extract_github_urls, plus host filtering so youtube/
    google/raw links never count as repos. Order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _REPO_URL_RE.finditer(text):
        host = m.group(1).lower()
        if host in _REPO_SKIP_HOSTS:
            continue
        owner, repo = m.group(2), m.group(3)
        repo = _TRAILING_PUNCT_RE.sub("", repo)
        if not repo or not owner:
            continue
        repo = repo.removesuffix(".git")
        url = f"https://{host}/{owner}/{repo}"
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _find_yt_dlp() -> Optional[str]:
    """`shutil.which` alone misses a pip-installed yt-dlp when the caller's
    PATH doesn't include the venv's bin dir (true of this app's systemd
    unit, which sets PATH to a bare system default) -- check next to the
    running interpreter first, since pip installs the console-script entry
    point there regardless of PATH."""
    candidate = Path(sys.executable).parent / "yt-dlp"
    if candidate.exists():
        return str(candidate)
    return shutil.which("yt-dlp")


def _try_yt_dlp(video_id: str) -> Optional[tuple[str, str]]:
    """Returns (title, description) via yt-dlp, or None if unavailable/failed."""
    binary = _find_yt_dlp()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "--dump-json", "--skip-download", "--no-warnings",
             f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        return data.get("title", ""), data.get("description", "") or ""
    except Exception:
        return None


# The description ships inside `ytInitialPlayerResponse = {...};` as a JSON
# blob on every public watch page — this is what a browser parses before any
# JS executes, so it needs no auth/quota, just an HTTP GET.
_PLAYER_RESPONSE_RE = re.compile(r"ytInitialPlayerResponse\s*=\s*(\{.*?\})\s*;\s*(?:var |</script>)")


def _try_http_page(video_id: str) -> Optional[tuple[str, str]]:
    """Returns (title, description) via a plain GET of the public watch
    page, or None on any failure. No API key, no auth."""
    try:
        resp = httpx.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=15, follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text
        m = _PLAYER_RESPONSE_RE.search(html)
        if not m:
            return None
        data = json.loads(m.group(1))
        details = data.get("videoDetails", {}) or {}
        title = details.get("title", "")
        description = details.get("shortDescription", "") or ""
        return title, description
    except Exception:
        return None


def fetch_video_description(url_or_id: str) -> VideoExtraction:
    """Fetch a video's title/description with no API key. Tries yt-dlp
    first (if installed), falls back to a plain HTTP GET of the public
    watch page. Always returns a VideoExtraction — check `.error` for
    failure, never raises."""
    video_id = extract_video_id(url_or_id)
    if not video_id:
        return VideoExtraction(
            video_id="", source_url=url_or_id,
            error=f"could not parse a YouTube video ID out of {url_or_id!r}",
        )

    result = _try_yt_dlp(video_id)
    method = "yt-dlp"
    if result is None:
        result = _try_http_page(video_id)
        method = "http"

    if result is None:
        return VideoExtraction(
            video_id=video_id, source_url=url_or_id,
            error="both yt-dlp and the HTTP-page fallback failed to fetch a description",
        )

    title, description = result
    return VideoExtraction(
        video_id=video_id, source_url=url_or_id,
        title=title, description=description, method=method,
        github_urls=extract_github_urls(description),
        repo_urls=extract_repo_urls(description),
    )
