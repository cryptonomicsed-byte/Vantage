"""Repository providers behind one interface (G6).

Before this, GitHub reads went through repo_host with one server token,
pushes and clones went to GITEA_URL with another, repo creation labelled
GitHub results as Gitea, and a workspace binding stored three strings that
nothing ever checked. This package is the one place that knows how to talk
to a repo host, and a binding is only a binding once the provider has
confirmed the repo exists.

    provider = get_provider("github")
    repo = await provider.get_repo(owner, name)        # None when unknown
    await provider.create_branch(owner, name, "guild/x/task", from_branch="main")
    pr = await provider.open_pr(owner, name, head=..., base=..., title=..., body=...)
    status = await provider.pr_status(owner, name, number)
    provider.verify_webhook(secret, raw_body, headers)

Credentials: a provider is constructed with a token. The GitHub token is
the deployment's (GITHUB_TOKEN); the Gitea token is the acting agent's own
account token where one exists (gitea_accounts), else the deployment's.
Nothing here reads a token from source.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import aiosqlite

from ..db import get_db

logger = logging.getLogger(__name__)

PROVIDERS = ("github", "gitea")


@dataclass
class Repo:
    provider: str
    owner: str
    name: str
    default_branch: str
    private: bool
    html_url: str
    clone_url: str
    permissions: dict

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class PullRequest:
    provider: str
    number: int
    html_url: str
    state: str          # open | closed | merged
    merged: bool
    head: str
    base: str
    merge_commit_sha: Optional[str] = None


class RepoProvider(Protocol):
    name: str

    async def get_repo(self, owner: str, name: str) -> Optional[Repo]: ...
    async def list_branches(self, owner: str, name: str) -> list[str]: ...
    async def create_branch(self, owner: str, name: str, branch: str, from_branch: str) -> dict: ...
    async def open_pr(self, owner: str, name: str, *, head: str, base: str, title: str, body: str) -> PullRequest: ...
    async def pr_status(self, owner: str, name: str, number: int) -> Optional[PullRequest]: ...
    def verify_webhook(self, secret: str, raw_body: bytes, headers: dict) -> bool: ...
    def web_url(self, owner: str, name: str) -> str: ...


class ProviderError(RuntimeError):
    """The host answered, and the answer was no (or nothing)."""


def hmac_sha256(secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def get_provider(name: str, token: Optional[str] = None) -> RepoProvider:
    name = (name or "github").lower()
    if name == "github":
        from .github import GitHubProvider
        return GitHubProvider(token=token)
    if name == "gitea":
        from .gitea import GiteaProvider
        return GiteaProvider(token=token)
    raise ValueError(f"unknown repo provider {name!r}; one of {', '.join(PROVIDERS)}")


async def init_repo_bindings_db() -> None:
    """Binding columns on guild_channels. Tolerant ALTERs."""
    async with get_db() as db:
        for ddl in (
            "ALTER TABLE guild_channels ADD COLUMN repo_provider TEXT DEFAULT NULL",
            "ALTER TABLE guild_channels ADD COLUMN repo_default_branch TEXT DEFAULT NULL",
            "ALTER TABLE guild_channels ADD COLUMN repo_credential_ref TEXT DEFAULT NULL",
            "ALTER TABLE guild_channels ADD COLUMN repo_webhook_id TEXT DEFAULT NULL",
            "ALTER TABLE guild_channels ADD COLUMN repo_verified_at TEXT DEFAULT NULL",
            "ALTER TABLE guild_channels ADD COLUMN repo_html_url TEXT DEFAULT NULL",
        ):
            try:
                await db.execute(ddl)
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS repo_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                delivery_id TEXT,
                event TEXT NOT NULL,
                repo TEXT NOT NULL,
                ref TEXT DEFAULT '',
                pr_number INTEGER,
                merged INTEGER DEFAULT 0,
                sha TEXT DEFAULT '',
                payload TEXT DEFAULT '',
                received_at TEXT DEFAULT (datetime('now')),
                UNIQUE (provider, delivery_id)
            )
        """)
        await db.commit()


def binding_of(channel: dict) -> Optional[dict]:
    """The repository binding on a workspace channel, or None."""
    owner, name = channel.get("repo_owner"), channel.get("repo_name")
    if not owner or not name:
        return None
    return {
        "provider": channel.get("repo_provider") or "github",
        "owner": owner, "name": name, "full_name": f"{owner}/{name}",
        "branch": channel.get("repo_default_branch") or channel.get("repo_branch") or "main",
        "verified_at": channel.get("repo_verified_at"),
        "html_url": channel.get("repo_html_url"),
        "webhook_id": channel.get("repo_webhook_id"),
    }
