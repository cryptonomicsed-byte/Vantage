"""GitHub-backed repo host, replacing the Gitea API calls in routers/code.py.

`routers/code.py` spoke Gitea (`{GITEA_URL}/api/v1/...`) and Gitea no longer
exists anywhere in the stack — so every /api/code/* endpoint returned empty
data. This module answers the same calls against the GitHub REST API and
normalises the three response shapes that differ, so the call sites in code.py
keep working unchanged:

    Gitea                          GitHub
    /repos/search      -> {data:[]}   /search/repositories -> {items:[]}
    stars_count                       stargazers_count
    branches[].commit.id              branches[].commit.sha

Query strings are parsed out of the "path" and translated (limit -> per_page).

Token: GITHUB_TOKEN (prefer a fine-grained, contents:read PAT scoped to the
org). Nothing here writes to GitHub unless POST is called explicitly.
"""
from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode

import httpx

GITHUB_API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "cryptonomicsed-byte")
TOKEN = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
TIMEOUT = float(os.environ.get("GITHUB_TIMEOUT", "5"))


class Response:
    """Minimal httpx.Response stand-in: callers use .status_code and .json()."""

    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def _headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vantage-repo-host",
    }
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _split(path: str):
    """"repos/x/y/commits?limit=3&sha=main" -> ("repos/x/y/commits", {...}, {limit:3, sha:main})"""
    raw = path.lstrip("/")
    q = ""
    if "?" in raw:
        raw, q = raw.split("?", 1)
    return raw, dict(parse_qsl(q))


def _translate(path: str) -> tuple[str, dict]:
    """Map a Gitea path+query to (github_path, params)."""
    raw, query = _split(path)

    if raw == "repos/search":
        # Gitea's global search; scope to the canonical owner so the UI lists
        # this organisation's repos rather than all of GitHub.
        q = query.get("q") or f"user:{GITHUB_OWNER}"
        params = {"q": q, "per_page": query.get("limit", 30), "sort": "updated"}
        return "/search/repositories", params

    params: dict = {}
    if "limit" in query:
        params["per_page"] = query.pop("limit")
    params.update(query)          # sha=, state=, etc. carry over verbatim
    return f"/{raw}", params


def _normalise(path: str, payload: Any) -> Any:
    """Reshape only where GitHub and Gitea actually differ."""
    raw, _q = _split(path)

    if raw == "repos/search" and isinstance(payload, dict):
        out = []
        for repo in payload.get("items", []) or []:
            repo = dict(repo)
            repo["stars_count"] = repo.get("stargazers_count", 0)
            out.append(repo)
        return {"data": out}

    if raw.endswith("/branches") and isinstance(payload, list):
        out = []
        for b in payload:
            b = dict(b)
            commit = dict(b.get("commit") or {})
            if "sha" in commit and "id" not in commit:
                commit["id"] = commit["sha"]
            b["commit"] = commit
            out.append(b)
        return out

    return payload


async def _request(method: str, path: str, json_body: Optional[dict] = None) -> Response:
    target, params = _translate(path)
    url = f"{GITHUB_API}{target}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.request(method, url, params=params, json=json_body,
                                     headers=_headers())
        try:
            payload = r.json()
        except Exception:
            payload = None
        # A private/unknown repo is a 404 from GitHub; surface it as-is.
        return Response(r.status_code, _normalise(path, payload))
    except Exception as exc:
        return Response(599, {"error": f"github request failed: {type(exc).__name__}"})


async def get(path: str, **kwargs) -> Response:
    """get("repos/{full_name}/commits?limit=3&sha=main") — headers ignored."""
    return await _request("GET", path)


async def post(path: str, json: Optional[dict] = None, **kwargs) -> Response:
    return await _request("POST", path, json_body=json)


def clone_url(full_name: str) -> str:
    return f"https://github.com/{full_name}.git"


def web_url(full_name: str) -> str:
    return f"https://github.com/{full_name}"
