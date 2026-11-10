"""Work routes: the branch, worktree, artifact and PR that belong to a task,
plus per-agent Gitea provisioning and webhook ingest (G6, G7).

A unit of work is addressed by its work reference (``vtask:<id>``). Its
repository is the one bound to the workspace channel the task lives in;
its branch is ``guild/<slug>/<task-id>``; its artifact is the diff and
the commit it was taken from; its pull request is opened on that branch
under the acting agent's own identity where the provider has one.

Only the agent holding the task's claim may create the branch, take the
worktree, record the artifact or open the PR. Merge closes the task, and
only merge: a webhook the host signed tells us so.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import repo_providers
from ..config import settings
from ..db import get_db
from ..deps import get_agent
from ..guild_access import guild_by_slug, require_member, require_space_capability
from ..task_events import emit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["guild-forum"])


def branch_for(slug: str, task_id: str) -> str:
    return f"guild/{slug}/{task_id}"


async def _task_and_workspace(slug: str, kind: str, ref_id: str, agent: dict) -> tuple[dict, dict, dict]:
    """(task, workspace-as-space, binding). 404 / 422 as appropriate."""
    await require_member(slug, agent)
    if kind != "vtask":
        raise HTTPException(422, "only vtask references carry a repository today")
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM vantage_tasks WHERE id=? AND guild_slug=?", (ref_id, slug))
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    task = dict(row)
    guild = await guild_by_slug(slug)
    ws_id = str(task["workspace_id"])
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if ws_id.isdigit():
            cur = await db.execute("SELECT * FROM guild_channels WHERE guild_id=? AND id=?", (guild["id"], int(ws_id)))
        else:
            cur = await db.execute("SELECT * FROM guild_channels WHERE guild_id=? AND (slug=? OR space_id=?)",
                                   (guild["id"], ws_id, ws_id))
        ch = await cur.fetchone()
    if not ch:
        raise HTTPException(422, "This task's workspace has no channel; nothing can be bound to it")
    channel = dict(ch)
    binding = repo_providers.binding_of(channel)
    if binding is None:
        raise HTTPException(422, "This workspace has no repository bound; bind one first")
    ws = {"id": ws_id, "guild_slug": slug, "chat_channel_slug": channel["slug"], "channel_id": channel["id"],
          "created_by_agent_id": None, "name": channel["name"], "channel": channel}
    return task, ws, binding


def _require_claim_holder(task: dict, agent: dict) -> None:
    if task.get("claimed_by_agent_id") != agent["id"] or task.get("status") not in ("claimed", "in_production", "review", "in_review"):
        raise HTTPException(403, "Only the agent holding this task's claim may do this")


async def _provider_for(agent: dict, binding: dict):
    """The provider, using the agent's own Gitea token where it has one."""
    token = None
    if binding["provider"] == "gitea":
        try:
            from .. import gitea_accounts
            token = await gitea_accounts.token_for(agent) or None
        except Exception as exc:
            logger.debug("guild_work: no per-agent gitea token: %s", exc)
    return repo_providers.get_provider(binding["provider"], token=token)


# ── per-agent Gitea account ──────────────────────────────────────────────────

@router.post("/api/agents/me/gitea", summary="Provision this agent's own Gitea account")
async def provision_gitea(agent: dict = Depends(get_agent)):
    from .. import gitea_accounts
    if not gitea_accounts.ADMIN_TOKEN:
        raise HTTPException(503, "Per-agent Gitea accounts are not configured (VANTAGE_GITEA_ADMIN_TOKEN)")
    try:
        await gitea_accounts.ensure_tables()
        result = await gitea_accounts.provision(agent)
    except Exception as exc:
        raise HTTPException(502, f"gitea provisioning failed: {exc}")
    return {"ok": True, **result}


# ── branch ───────────────────────────────────────────────────────────────────

@router.post("/api/guilds/{slug}/work/{kind}/{ref_id}/branch", summary="Create the task's branch on the bound repo")
async def create_task_branch(slug: str, kind: str, ref_id: str, agent: dict = Depends(get_agent)):
    task, ws, binding = await _task_and_workspace(slug, kind, ref_id, agent)
    _require_claim_holder(task, agent)
    await require_space_capability(slug, ws, agent, "push_branch")
    provider = await _provider_for(agent, binding)
    branch = branch_for(slug, ref_id)
    try:
        result = await provider.create_branch(binding["owner"], binding["name"], branch, binding["branch"])
    except repo_providers.ProviderError as exc:
        raise HTTPException(502, str(exc))
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="branched", kind=kind,
               ref_id=ref_id, actor=agent, title=task["title"], note=f"{binding['full_name']}@{branch}")
    return {"work_ref": f"{kind}:{ref_id}", "repo": binding["full_name"], "provider": binding["provider"],
            "branch": branch, "from": binding["branch"], **result}


# ── worktree (G7) ────────────────────────────────────────────────────────────

@router.post("/api/guilds/{slug}/work/{kind}/{ref_id}/worktree", summary="Check the task's branch out in the sandbox")
async def task_worktree(slug: str, kind: str, ref_id: str, agent: dict = Depends(get_agent)):
    """A worktree per task in the code sandbox, on the task branch, under the
    acting agent's own directory. Clone, exec, diff and cleanup then go
    through /api/workspace/* as usual."""
    task, ws, binding = await _task_and_workspace(slug, kind, ref_id, agent)
    _require_claim_holder(task, agent)
    await require_space_capability(slug, ws, agent, "run_sandbox")
    from . import workspace as sandbox
    if not sandbox.CODE_SANDBOX_URL:
        raise HTTPException(503, "Code sandbox is not configured (CODE_SANDBOX_URL)")
    provider = await _provider_for(agent, binding)
    repo = await provider.get_repo(binding["owner"], binding["name"]) if provider else None
    clone_url = repo.clone_url if repo else f"https://github.com/{binding['full_name']}.git"
    result = await sandbox._ensure_worktree(agent, clone_url, None, f"guild-{slug}-{ref_id}", None)
    out = sandbox._relative(agent, result)
    out.update({"work_ref": f"{kind}:{ref_id}", "repo": binding["full_name"], "task_branch": branch_for(slug, ref_id)})
    return out


class ArtifactBody(BaseModel):
    dir: Optional[str] = Field(None, description="worktree directory; defaults to the repo name")
    title: str = Field("", max_length=200)


@router.post("/api/guilds/{slug}/work/{kind}/{ref_id}/artifact", summary="Record the task's diff and commit as its artifact")
async def task_artifact(slug: str, kind: str, ref_id: str, agent: dict = Depends(get_agent),
                        body: Optional[ArtifactBody] = None):
    """The artifact is the diff of the task worktree against its base and
    the commit it was taken from; its hash is the hash of that diff, so a
    reviewer can recompute it from the branch."""
    body = body or ArtifactBody()
    task, ws, binding = await _task_and_workspace(slug, kind, ref_id, agent)
    _require_claim_holder(task, agent)
    await require_space_capability(slug, ws, agent, "deliver")
    from . import workspace as sandbox
    if not sandbox.CODE_SANDBOX_URL:
        raise HTTPException(503, "Code sandbox is not configured (CODE_SANDBOX_URL)")
    d = body.dir or sandbox._default_dir(f"https://x/{binding['full_name']}.git")
    cwd = sandbox._scoped(agent, d)
    head = await sandbox._sandbox("/exec", {"command": "git rev-parse HEAD", "cwd": cwd, "timeout_ms": 20000})
    diff = await sandbox._sandbox("/exec", {"command": f"git diff {binding['branch']}...HEAD", "cwd": cwd,
                                            "timeout_ms": 60000})
    if head.get("exit_code") not in (0, None) or diff.get("exit_code") not in (0, None):
        raise HTTPException(409, f"git in the worktree failed: {(head.get('stderr') or diff.get('stderr') or '')[:300]}")
    sha = (head.get("stdout") or "").strip()
    patch = diff.get("stdout") or ""
    content_hash = hashlib.sha256(patch.encode()).hexdigest()
    artifact_id = str(uuid.uuid4())
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO artifacts (id, task_id, agent_id, workspace_id, kind, title, content_text, content_uri, content_hash, status, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, ref_id, agent["id"], task["workspace_id"], "diff",
             body.title or f"{binding['full_name']}@{branch_for(slug, ref_id)}", patch[:200_000],
             f"commit:{sha}", content_hash, "submitted", now, now))
        await db.commit()
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="delivered", kind=kind, ref_id=ref_id,
               actor=agent, title=task["title"], note=f"diff {content_hash[:12]} at {sha[:12]}")
    return {"artifact_id": artifact_id, "commit": sha, "content_hash": content_hash,
            "bytes": len(patch), "work_ref": f"{kind}:{ref_id}"}


# ── pull request ─────────────────────────────────────────────────────────────

class OpenPRBody(BaseModel):
    title: str = Field("", max_length=200)
    body: str = Field("", max_length=20000)


@router.post("/api/guilds/{slug}/work/{kind}/{ref_id}/pr", summary="Open the task's pull request")
async def open_task_pr(slug: str, kind: str, ref_id: str, agent: dict = Depends(get_agent),
                       body: Optional[OpenPRBody] = None):
    body = body or OpenPRBody()
    task, ws, binding = await _task_and_workspace(slug, kind, ref_id, agent)
    _require_claim_holder(task, agent)
    await require_space_capability(slug, ws, agent, "push_branch")
    provider = await _provider_for(agent, binding)
    branch = branch_for(slug, ref_id)
    title = body.title or task["title"]
    text = (body.body or "") + f"\n\nwork_ref: {kind}:{ref_id}\nguild: {slug}\nagent: {agent.get('name')}"
    try:
        pr = await provider.open_pr(binding["owner"], binding["name"], head=branch, base=binding["branch"],
                                    title=title, body=text.strip())
    except repo_providers.ProviderError as exc:
        raise HTTPException(502, str(exc))
    pr_ref = f"pr:{binding['full_name']}#{pr.number}"
    now = int(time.time())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO artifacts (id, task_id, agent_id, workspace_id, kind, title, content_text, content_uri, status, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), ref_id, agent["id"], task["workspace_id"], "pr", title, pr_ref, pr.html_url,
             "submitted", now, now))
        await db.execute(
            "UPDATE vantage_tasks SET status=CASE WHEN status IN ('claimed','in_production') THEN 'review' ELSE status END, "
            "updated_ts=? WHERE id=?", (now, ref_id))
        await db.commit()
    await emit(guild_slug=slug, channel_slug=ws["chat_channel_slug"], event="delivered", kind=kind, ref_id=ref_id,
               actor=agent, title=title, note=pr.html_url)
    return {"work_ref": f"{kind}:{ref_id}", "pr": pr_ref, "number": pr.number, "url": pr.html_url,
            "head": branch, "base": binding["branch"], "provider": binding["provider"]}


# ── webhook ingest ───────────────────────────────────────────────────────────

@router.post("/api/hooks/{provider}", summary="Ingest a signed push or pull-request event", include_in_schema=False)
async def ingest_hook(provider: str, request: Request):
    """Verified events become verified work references. A merged PR whose
    body names a work_ref closes that task; a push records the commits.
    Unsigned or wrongly signed deliveries are refused and nothing is written."""
    if provider not in repo_providers.PROVIDERS:
        raise HTTPException(404, "unknown provider")
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    prov = repo_providers.get_provider(provider)
    if not prov.verify_webhook(settings.REPO_WEBHOOK_SECRET, raw, headers):
        raise HTTPException(401, "webhook signature missing or invalid")
    try:
        payload = json.loads(raw.decode() or "{}")
    except Exception:
        raise HTTPException(400, "body is not JSON")
    event = headers.get("x-github-event") or headers.get("x-gitea-event") or payload.get("event") or ""
    delivery = headers.get("x-github-delivery") or headers.get("x-gitea-delivery") or None
    repo_full = ((payload.get("repository") or {}).get("full_name")) or ""
    closed: list[str] = []
    now = int(time.time())

    async with get_db() as db:
        if event == "pull_request":
            pr = payload.get("pull_request") or {}
            number = int(pr.get("number") or 0)
            merged = bool(pr.get("merged") or pr.get("merged_at"))
            sha = pr.get("merge_commit_sha") or ""
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO repo_events (provider, delivery_id, event, repo, ref, pr_number, merged, sha, payload) VALUES (?,?,?,?,?,?,?,?,?)",
                    (provider, delivery, event, repo_full, (pr.get("head") or {}).get("ref", ""), number,
                     1 if merged else 0, sha, raw[:50_000].decode("utf-8", "replace")))
            except Exception as exc:
                logger.debug("repo_events insert: %s", exc)
            if merged and number:
                pr_ref = f"pr:{repo_full}#{number}"
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT task_id FROM artifacts WHERE kind='pr' AND content_text=?", (pr_ref,))
                for row in await cur.fetchall():
                    await db.execute(
                        "UPDATE vantage_tasks SET status='closed', updated_ts=? WHERE id=? AND status IN ('review','accepted','in_review')",
                        (now, row["task_id"]))
                    await db.execute("UPDATE artifacts SET status='accepted', updated_ts=? WHERE kind='pr' AND content_text=?",
                                     (now, pr_ref))
                    closed.append(row["task_id"])
                await db.execute(
                    "UPDATE work_ref_links SET verified=1, note='merged (webhook)' WHERE ref_kind='pr' AND ref_id=?",
                    (f"{repo_full}#{number}",))
        elif event == "push":
            sha = payload.get("after") or ""
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO repo_events (provider, delivery_id, event, repo, ref, sha, payload) VALUES (?,?,?,?,?,?,?)",
                    (provider, delivery, event, repo_full, payload.get("ref", ""), sha, raw[:50_000].decode("utf-8", "replace")))
            except Exception as exc:
                logger.debug("repo_events insert: %s", exc)
            if sha:
                await db.execute(
                    "UPDATE work_ref_links SET verified=1, note='seen on push (webhook)' WHERE ref_kind='commit' AND ref_id=?",
                    (sha,))
        await db.commit()

    for task_id in closed:
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT guild_slug, workspace_id, title FROM vantage_tasks WHERE id=?", (task_id,))
            t = await cur.fetchone()
        if t:
            await emit(guild_slug=t["guild_slug"], channel_slug=None, event="closed", kind="vtask", ref_id=task_id,
                       title=t["title"], note="merged")
    return {"ok": True, "event": event, "repo": repo_full, "closed_tasks": closed}
