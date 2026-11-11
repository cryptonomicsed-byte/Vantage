"""Gitea implementation of the repo provider interface."""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from . import ProviderError, PullRequest, Repo, hmac_sha256

GITEA_URL = (os.environ.get("VANTAGE_GITEA_URL") or os.environ.get("GITEA_URL") or "").rstrip("/")
TIMEOUT = float(os.environ.get("VANTAGE_GITEA_TIMEOUT", "15"))


def deployment_token() -> str:
    return (os.environ.get("VANTAGE_GITEA_TOKEN") or os.environ.get("GITEA_TOKEN") or "").strip()


class GiteaProvider:
    name = "gitea"

    def __init__(self, token: Optional[str] = None, *, base_url: Optional[str] = None, transport=None):
        self.base = (base_url or GITEA_URL).rstrip("/")
        self.token = token or deployment_token()
        self._transport = transport

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"token {self.token}"
        return h

    async def _call(self, method: str, path: str, json: Optional[dict] = None) -> tuple[int, Any]:
        if self._transport is not None:
            return await self._transport(method, path, json)
        if not self.base:
            raise ProviderError("gitea is not configured (VANTAGE_GITEA_URL)")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
                r = await cl.request(method, f"{self.base}/api/v1{path}", json=json, headers=self._headers())
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, None
        except Exception as exc:
            raise ProviderError(f"gitea unreachable: {type(exc).__name__}") from exc

    def web_url(self, owner: str, name: str) -> str:
        return f"{self.base}/{owner}/{name}"

    async def get_repo(self, owner: str, name: str) -> Optional[Repo]:
        status, data = await self._call("GET", f"/repos/{owner}/{name}")
        if status == 404 or not isinstance(data, dict):
            return None
        if status != 200:
            raise ProviderError(f"gitea repo lookup failed: {status}")
        return Repo(provider="gitea", owner=owner, name=name,
                    default_branch=data.get("default_branch") or "main",
                    private=bool(data.get("private")), html_url=data.get("html_url") or self.web_url(owner, name),
                    clone_url=data.get("clone_url") or f"{self.base}/{owner}/{name}.git",
                    permissions=dict(data.get("permissions") or {}))

    async def list_branches(self, owner: str, name: str) -> list[str]:
        status, data = await self._call("GET", f"/repos/{owner}/{name}/branches?limit=100")
        if status != 200 or not isinstance(data, list):
            raise ProviderError(f"gitea branches failed: {status}")
        return [b.get("name") for b in data if b.get("name")]

    async def create_branch(self, owner: str, name: str, branch: str, from_branch: str) -> dict:
        status, data = await self._call("POST", f"/repos/{owner}/{name}/branches",
                                        {"new_branch_name": branch, "old_branch_name": from_branch})
        if status == 409:
            return {"branch": branch, "created": False}
        if status not in (200, 201):
            raise ProviderError(f"gitea: branch create failed ({status})")
        return {"branch": branch, "sha": (data or {}).get("commit", {}).get("id") if isinstance(data, dict) else None,
                "created": True}

    async def open_pr(self, owner: str, name: str, *, head: str, base: str, title: str, body: str) -> PullRequest:
        status, data = await self._call("POST", f"/repos/{owner}/{name}/pulls",
                                        {"title": title, "body": body, "head": head, "base": base})
        if status not in (200, 201) or not isinstance(data, dict):
            raise ProviderError(f"gitea: PR open failed ({status}): {str(data)[:160]}")
        return PullRequest(provider="gitea", number=int(data["number"]), html_url=data.get("html_url", ""),
                           state=data.get("state", "open"), merged=bool(data.get("merged")), head=head, base=base)

    async def pr_status(self, owner: str, name: str, number: int) -> Optional[PullRequest]:
        status, data = await self._call("GET", f"/repos/{owner}/{name}/pulls/{number}")
        if status == 404 or not isinstance(data, dict):
            return None
        merged = bool(data.get("merged"))
        return PullRequest(provider="gitea", number=number, html_url=data.get("html_url", ""),
                           state="merged" if merged else data.get("state", "open"), merged=merged,
                           head=(data.get("head") or {}).get("ref", ""), base=(data.get("base") or {}).get("ref", ""),
                           merge_commit_sha=data.get("merge_commit_sha"))

    def verify_webhook(self, secret: str, raw_body: bytes, headers: dict) -> bool:
        sig = headers.get("x-gitea-signature") or headers.get("X-Gitea-Signature") or ""
        if not secret or not sig:
            return False
        import hmac as _hmac
        return _hmac.compare_digest(sig, hmac_sha256(secret, raw_body))
