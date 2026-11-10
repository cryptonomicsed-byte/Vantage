"""GitHub implementation of the repo provider interface."""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from . import ProviderError, PullRequest, Repo, hmac_sha256

GITHUB_API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
TIMEOUT = float(os.environ.get("GITHUB_TIMEOUT", "8"))


def deployment_token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


class GitHubProvider:
    name = "github"

    def __init__(self, token: Optional[str] = None, *, transport=None):
        self.token = token or deployment_token()
        # `transport` is an async callable (method, url, json) -> (status, payload)
        # so tests can stand in for the network without patching httpx.
        self._transport = transport

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "vantage-guild"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _call(self, method: str, path: str, json: Optional[dict] = None) -> tuple[int, Any]:
        if self._transport is not None:
            return await self._transport(method, path, json)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
                r = await cl.request(method, f"{GITHUB_API}{path}", json=json, headers=self._headers())
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, None
        except Exception as exc:
            raise ProviderError(f"github unreachable: {type(exc).__name__}") from exc

    def web_url(self, owner: str, name: str) -> str:
        return f"https://github.com/{owner}/{name}"

    async def get_repo(self, owner: str, name: str) -> Optional[Repo]:
        status, data = await self._call("GET", f"/repos/{owner}/{name}")
        if status == 404 or not isinstance(data, dict):
            return None
        if status != 200:
            raise ProviderError(f"github repo lookup failed: {status}")
        return Repo(provider="github", owner=owner, name=name,
                    default_branch=data.get("default_branch") or "main",
                    private=bool(data.get("private")), html_url=data.get("html_url") or self.web_url(owner, name),
                    clone_url=data.get("clone_url") or f"https://github.com/{owner}/{name}.git",
                    permissions=dict(data.get("permissions") or {}))

    async def list_branches(self, owner: str, name: str) -> list[str]:
        status, data = await self._call("GET", f"/repos/{owner}/{name}/branches?per_page=100")
        if status != 200 or not isinstance(data, list):
            raise ProviderError(f"github branches failed: {status}")
        return [b.get("name") for b in data if b.get("name")]

    async def create_branch(self, owner: str, name: str, branch: str, from_branch: str) -> dict:
        status, data = await self._call("GET", f"/repos/{owner}/{name}/git/ref/heads/{from_branch}")
        if status != 200 or not isinstance(data, dict):
            raise ProviderError(f"github: base branch {from_branch!r} not found ({status})")
        sha = (data.get("object") or {}).get("sha")
        status, data = await self._call("POST", f"/repos/{owner}/{name}/git/refs",
                                        {"ref": f"refs/heads/{branch}", "sha": sha})
        if status == 422:
            return {"branch": branch, "sha": sha, "created": False}  # already exists
        if status not in (200, 201):
            raise ProviderError(f"github: branch create failed ({status})")
        return {"branch": branch, "sha": sha, "created": True}

    async def open_pr(self, owner: str, name: str, *, head: str, base: str, title: str, body: str) -> PullRequest:
        status, data = await self._call("POST", f"/repos/{owner}/{name}/pulls",
                                        {"title": title, "body": body, "head": head, "base": base})
        if status not in (200, 201) or not isinstance(data, dict):
            raise ProviderError(f"github: PR open failed ({status}): {str(data)[:160]}")
        return PullRequest(provider="github", number=int(data["number"]), html_url=data.get("html_url", ""),
                           state=data.get("state", "open"), merged=bool(data.get("merged")), head=head, base=base)

    async def pr_status(self, owner: str, name: str, number: int) -> Optional[PullRequest]:
        status, data = await self._call("GET", f"/repos/{owner}/{name}/pulls/{number}")
        if status == 404 or not isinstance(data, dict):
            return None
        merged = bool(data.get("merged") or data.get("merged_at"))
        return PullRequest(provider="github", number=number, html_url=data.get("html_url", ""),
                           state="merged" if merged else data.get("state", "open"), merged=merged,
                           head=(data.get("head") or {}).get("ref", ""), base=(data.get("base") or {}).get("ref", ""),
                           merge_commit_sha=data.get("merge_commit_sha"))

    def verify_webhook(self, secret: str, raw_body: bytes, headers: dict) -> bool:
        sig = (headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256") or "")
        if not secret or not sig.startswith("sha256="):
            return False
        import hmac as _hmac
        return _hmac.compare_digest(sig[7:], hmac_sha256(secret, raw_body))
