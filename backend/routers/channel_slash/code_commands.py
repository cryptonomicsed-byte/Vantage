"""code_commands.py -- slash command handlers for code collab channels.

All commands are async, fail gracefully (return error dict, not exception),
and operate on per-contributor git branches so multiple contributors never
stomp each other on main.

Branch naming: {channel_slug}/{agent_id}   e.g. my-code-channel/5
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── shared helpers ──────────────────────────────────────────────────────────

def _ok(reply: str, **data) -> dict:
    return {"reply": reply, "data": data}

def _err(msg: str) -> dict:
    return {"reply": f":x: {msg}", "data": {"error": msg}}

def _no_repo_msg() -> dict:
    return _err(
        "This code channel has no repo bound. "
        "Ask an admin to bind one with /bind <owner>/<repo>"
    )


def _get_code_router():
    """Return shared git helpers from code.py to avoid duplicating credential logic."""
    from ..code import (  # type: ignore
        GITEA_URL, GITEA_TOKEN, SCAN_DIR,
        _bare_clone_url, _git_clone_env, _headers,
    )
    return GITEA_URL, GITEA_TOKEN, SCAN_DIR, _bare_clone_url, _git_clone_env, _headers


async def _get_or_create_session(db, channel_id: int, agent_id: int, channel: dict) -> Optional[dict]:
    """Return (or create) the channel_code_sessions row for this contributor."""
    owner = channel.get("repo_owner") or ""
    name  = channel.get("repo_name") or ""
    channel_slug = channel.get("slug") or channel.get("name") or str(channel_id)
    branch_name  = f"{channel_slug}/{agent_id}"

    row = await (await db.execute(
        "SELECT * FROM channel_code_sessions WHERE channel_id=? AND contributor_id=?",
        (channel_id, agent_id),
    )).fetchone()

    if row:
        return dict(row)

    # Create new session row
    await db.execute(
        """INSERT INTO channel_code_sessions
           (channel_id, contributor_id, contributor_type, branch_name,
            status, repo_owner, repo_name)
           VALUES (?, ?, 'agent', ?, 'active', ?, ?)""",
        (channel_id, agent_id, branch_name, owner, name),
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM channel_code_sessions WHERE channel_id=? AND contributor_id=?",
        (channel_id, agent_id),
    )).fetchone()
    return dict(row) if row else None


async def _ensure_branch(owner: str, name: str, branch_name: str) -> bool:
    """Create the branch on the remote if it does not exist.  Returns True on success."""
    GITEA_URL, GITEA_TOKEN, SCAN_DIR, _bare_clone_url, _git_clone_env, _headers = _get_code_router()
    full_name = f"{owner}/{name}"
    clone_url = _bare_clone_url(full_name)
    target = os.path.join(SCAN_DIR, f"branch_{name}_{int(time.time())}")
    os.makedirs(SCAN_DIR, exist_ok=True)
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, target],
            capture_output=True, timeout=30, env=_git_clone_env(),
        )
        if r.returncode != 0:
            return False
        check = subprocess.run(
            ["git", "-C", target, "ls-remote", "--heads", "origin", branch_name],
            capture_output=True, timeout=10, env=_git_clone_env(),
        )
        if branch_name.encode() in (check.stdout or b""):
            return True
        subprocess.run(
            ["git", "-C", target, "checkout", "-b", branch_name],
            capture_output=True,
        )
        push = subprocess.run(
            ["git", "-C", target, "push", "origin", branch_name],
            capture_output=True, timeout=30, env=_git_clone_env(),
        )
        return push.returncode == 0
    except Exception as e:
        logger.warning("_ensure_branch failed: %s", e)
        return False
    finally:
        shutil.rmtree(target, ignore_errors=True)


async def _call_llm(prompt: str, system: str = "You are a senior software engineer.") -> str:
    """Call the instance OmniRoute (OpenAI-compatible) to generate code.

    Falls back to a stub response if OmniRoute is unreachable so the command
    never hard-crashes.
    """
    try:
        from ...config import settings  # type: ignore
        base_url = (getattr(settings, "OMNIROUTE_URL", None) or "http://localhost:8300").rstrip("/")
        api_key  = getattr(settings, "OMNIROUTE_API_KEY", None) or "no-key"
        model    = getattr(settings, "OMNIROUTE_MODEL", None) or "auto"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "max_tokens": 2048,
        }
        async with httpx.AsyncClient(timeout=60) as cl:
            r = await cl.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning("LLM call failed (%s), returning placeholder", e)
        return f"# LLM unavailable ({e})\n# TODO: implement based on prompt: {prompt[:200]}\n"


async def _push_to_branch(owner: str, name: str, branch: str, path: str, content: str, message: str) -> dict:
    """Write content to path on branch.  Returns a status dict."""
    GITEA_URL, GITEA_TOKEN, SCAN_DIR, _bare_clone_url, _git_clone_env, _headers = _get_code_router()
    full_name = f"{owner}/{name}"
    clone_url = _bare_clone_url(full_name)
    target = os.path.join(SCAN_DIR, f"push_{name}_{int(time.time())}")
    os.makedirs(SCAN_DIR, exist_ok=True)
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, clone_url, target],
            capture_output=True, timeout=30, env=_git_clone_env(),
        )
        if r.returncode != 0:
            r = subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, target],
                capture_output=True, timeout=30, env=_git_clone_env(),
            )
            subprocess.run(
                ["git", "-C", target, "checkout", "-b", branch],
                capture_output=True,
            )

        filepath = os.path.join(target, path)
        os.makedirs(os.path.dirname(os.path.join(target, path)), exist_ok=True)
        with open(filepath, "w") as fh:
            fh.write(content)

        subprocess.run(["git", "-C", target, "config", "user.email", "agent@vantage.local"], capture_output=True)
        subprocess.run(["git", "-C", target, "config", "user.name", "Vantage Agent"], capture_output=True)
        subprocess.run(["git", "-C", target, "add", path], capture_output=True)
        subprocess.run(["git", "-C", target, "commit", "-m", message], capture_output=True)
        push = subprocess.run(
            ["git", "-C", target, "push", "origin", branch],
            capture_output=True, timeout=30, env=_git_clone_env(),
        )
        sha_r = subprocess.run(
            ["git", "-C", target, "rev-parse", "HEAD"],
            capture_output=True,
        )
        sha = sha_r.stdout.decode().strip()[:12] if sha_r.returncode == 0 else "unknown"
        return {"ok": push.returncode == 0, "sha": sha, "path": path, "branch": branch}
    except Exception as e:
        logger.warning("_push_to_branch error: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        shutil.rmtree(target, ignore_errors=True)


async def _get_commits(owner: str, name: str, branch: str, limit: int = 5) -> list:
    """Fetch recent commits on a branch via the Gitea API."""
    try:
        GITEA_URL, _, _, _, _, _headers = _get_code_router()
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.get(
                f"{GITEA_URL}/api/v1/repos/{owner}/{name}/commits",
                params={"sha": branch, "limit": limit},
                headers=_headers,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.debug("_get_commits error: %s", e)
    return []


# ── command handlers ────────────────────────────────────────────────────────

async def _cmd_run(args: str, channel: dict, agent: dict, db) -> dict:
    """Generate code from a prompt and push it to the contributor's branch."""
    if not args:
        return _err("Usage: /run <prompt>")

    owner = channel.get("repo_owner") or ""
    name  = channel.get("repo_name") or ""
    if not (owner and name):
        return _no_repo_msg()

    channel_id   = channel["id"]
    agent_id     = agent["id"]

    session = await _get_or_create_session(db, channel_id, agent_id, channel)
    if not session:
        return _err("Could not create a session for this contributor.")

    branch = session["branch_name"]

    repo_context = f"Repository: {owner}/{name}, branch: {branch}"
    code_prompt  = (
        f"{repo_context}\n\n"
        f"Task: {args}\n\n"
        "Generate clean, production-ready code for the task above. "
        "Reply with ONLY the file content. No markdown fences unless the file is Markdown. "
        "Start with a comment naming the file path on the first line "
        "(e.g. `# path: src/foo.py`)."
    )

    generated = await _call_llm(code_prompt)

    file_path = "generated/output.py"
    for line in generated.split("\n")[:3]:
        stripped = line.strip().lstrip("#").lstrip("//").strip()
        if stripped.lower().startswith("path:"):
            candidate = stripped[5:].strip()
            if candidate and ("/" in candidate or "." in candidate):
                file_path = candidate
            break

    commit_msg  = f"[/run] {args[:60]}"
    push_result = await _push_to_branch(owner, name, branch, file_path, generated, commit_msg)

    if not push_result.get("ok"):
        preview = generated[:600] + ("\n...(truncated)" if len(generated) > 600 else "")
        return _ok(
            f"Code generated but push failed: {push_result.get('error', 'unknown')}.\n\n"
            f"```\n{preview}\n```",
            branch=branch, file_path=file_path, push_ok=False,
        )

    preview = generated[:800] + ("\n...(truncated)" if len(generated) > 800 else "")

    await db.execute(
        "UPDATE channel_code_sessions SET last_commit_sha=? WHERE channel_id=? AND contributor_id=?",
        (push_result["sha"], channel_id, agent_id),
    )
    await db.commit()

    try:
        from backend.utils import _append_receipt
        await _append_receipt(
            str(agent_id), "code.execute",
            {"verb": "run", "branch": branch, "prompt": args[:100], "file": file_path},
        )
    except Exception:
        pass  # fail-open

    return _ok(
        f"Code pushed to `{branch}` -- `{file_path}` (sha `{push_result['sha']}`)\n\n"
        f"```\n{preview}\n```",
        branch=branch, file_path=file_path, sha=push_result["sha"], push_ok=True,
    )


async def _cmd_pr(args: str, channel: dict, agent: dict, db) -> dict:
    """Open a PR from the contributor's branch to main."""
    owner = channel.get("repo_owner") or ""
    name  = channel.get("repo_name") or ""
    if not (owner and name):
        return _no_repo_msg()

    channel_id = channel["id"]
    agent_id   = agent["id"]
    session    = await (await db.execute(
        "SELECT * FROM channel_code_sessions WHERE channel_id=? AND contributor_id=?",
        (channel_id, agent_id),
    )).fetchone()
    if not session:
        return _err("No active session. Run /run first to start working on a branch.")

    branch = session["branch_name"]
    title  = args.strip() or f"PR from {branch}"

    try:
        GITEA_URL, _, _, _, _, _headers = _get_code_router()
        payload = {
            "title": title,
            "body": (
                f"Opened by contributor {agent.get('name', agent_id)} "
                "via /pr in code channel."
            ),
            "head": branch,
            "base": "main",
        }
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.post(
                f"{GITEA_URL}/api/v1/repos/{owner}/{name}/pulls",
                json=payload,
                headers=_headers,
            )
            if r.status_code in (200, 201):
                data   = r.json()
                pr_url = (
                    data.get("html_url")
                    or f"{GITEA_URL}/{owner}/{name}/pulls/{data.get('number')}"
                )
                return _ok(f"PR created: {pr_url}", pr_url=pr_url, number=data.get("number"))
            else:
                return _err(f"Gitea returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        return _err(f"PR creation failed: {e}")


async def _cmd_scan(args: str, channel: dict, agent: dict, db) -> dict:
    """Trigger an async security scan on the contributor's current branch."""
    owner = channel.get("repo_owner") or ""
    name  = channel.get("repo_name") or ""
    if not (owner and name):
        return _no_repo_msg()

    channel_id = channel["id"]
    agent_id   = agent["id"]
    session    = await (await db.execute(
        "SELECT * FROM channel_code_sessions WHERE channel_id=? AND contributor_id=?",
        (channel_id, agent_id),
    )).fetchone()
    if not session:
        return _err("No active session. Run /run first to start working on a branch.")

    try:
        from ..code import trigger_scan, ScanRequest  # type: ignore
        fake_agent = {"id": agent_id, "name": agent.get("name", str(agent_id))}
        scan_req   = ScanRequest(engine="auto")
        result     = await trigger_scan(owner, name, scan_req, fake_agent)
        scan_id    = result.get("scan_id") or result.get("id") or "?"
        return _ok(
            f"Security scan started (scan_id `{scan_id}`). "
            f"Poll with: GET /api/code/repo/{owner}/{name}/scan/{scan_id}",
            scan_id=scan_id,
        )
    except Exception as e:
        logger.warning("/scan command error: %s", e)
        return _err(f"Failed to trigger scan: {e}")


async def _cmd_review(args: str, channel: dict, agent: dict, db) -> dict:
    """Show recent commits on a branch (default: contributor's own branch)."""
    owner = channel.get("repo_owner") or ""
    name  = channel.get("repo_name") or ""
    if not (owner and name):
        return _no_repo_msg()

    channel_id = channel["id"]
    agent_id   = agent["id"]

    if args.strip():
        branch = args.strip()
    else:
        session = await (await db.execute(
            "SELECT branch_name FROM channel_code_sessions WHERE channel_id=? AND contributor_id=?",
            (channel_id, agent_id),
        )).fetchone()
        if not session:
            return _err("No active session and no branch specified.")
        branch = session["branch_name"]

    commits = await _get_commits(owner, name, branch, limit=5)
    if not commits:
        return _ok(
            f"No commits found on branch `{branch}` (or branch does not exist).",
            branch=branch,
        )

    lines = [
        f"**Recent commits on `{branch}`:**",
        "| SHA | Message | Author |",
        "|---|---|---|",
    ]
    for c in commits:
        sha    = (c.get("sha") or "")[:8]
        msg    = (c.get("commit", {}).get("message") or "").split("\n")[0][:72]
        author = c.get("commit", {}).get("author", {}).get("name", "?")
        lines.append(f"| `{sha}` | {msg} | {author} |")
    return _ok(
        "\n".join(lines),
        branch=branch,
        commits=[c.get("sha", "")[:8] for c in commits],
    )


async def _cmd_merge(args: str, channel: dict, agent: dict, db) -> dict:
    """Merge a branch to main. Admin only (or contributor's own branch after scan pass)."""
    owner = channel.get("repo_owner") or ""
    name  = channel.get("repo_name") or ""
    if not (owner and name):
        return _no_repo_msg()

    channel_id = channel["id"]
    agent_id   = agent["id"]

    if args.strip():
        branch = args.strip()
    else:
        session = await (await db.execute(
            "SELECT branch_name FROM channel_code_sessions WHERE channel_id=? AND contributor_id=?",
            (channel_id, agent_id),
        )).fetchone()
        if not session:
            return _err("No active session and no branch specified.")
        branch = session["branch_name"]

    role_row = await (await db.execute(
        """SELECT gcr.role FROM guild_channel_roster gcr
           WHERE gcr.channel_id=? AND gcr.principal_id=?""",
        (channel_id, agent_id),
    )).fetchone()
    is_admin      = role_row and role_row["role"] in ("admin", "founder", "moderator")
    is_own_branch = branch.endswith(f"/{agent_id}")

    if not is_admin and not is_own_branch:
        return _err("Only admins (or the branch owner) can merge a branch.")

    if is_own_branch and not is_admin:
        try:
            from ..code import _get_latest_scan  # type: ignore
            latest = await _get_latest_scan(owner, name)
            if latest:
                findings = json.loads(latest.get("findings_json") or "[]")
                critical = [f for f in findings if f.get("severity", 0) >= 0.90]
                if critical:
                    return _err(
                        f"{len(critical)} critical finding(s) block this merge. "
                        "Fix them and rescan before merging."
                    )
        except Exception as e:
            logger.debug("Scan gate check failed: %s", e)

    try:
        GITEA_URL, _, _, _, _, _headers = _get_code_router()
        async with httpx.AsyncClient(timeout=10) as cl:
            pr_r  = await cl.get(
                f"{GITEA_URL}/api/v1/repos/{owner}/{name}/pulls",
                params={"state": "open", "head": branch},
                headers=_headers,
            )
            pulls = pr_r.json() if pr_r.status_code == 200 else []
            if not pulls:
                return _err(
                    f"No open PR found for branch `{branch}`. Open one with /pr first."
                )
            pr_num  = pulls[0]["number"]
            merge_r = await cl.post(
                f"{GITEA_URL}/api/v1/repos/{owner}/{name}/pulls/{pr_num}/merge",
                json={
                    "Do": "merge",
                    "merge_message_field": (
                        f"Merged via /merge by {agent.get('name', agent_id)}"
                    ),
                },
                headers=_headers,
            )
            if merge_r.status_code in (200, 204):
                await db.execute(
                    """UPDATE channel_code_sessions SET status='merged'
                       WHERE channel_id=? AND contributor_id=? AND branch_name=?""",
                    (channel_id, agent_id, branch),
                )
                await db.commit()
                try:
                    from backend.utils import _append_receipt
                    await _append_receipt(
                        str(agent_id), "code.execute",
                        {"verb": "merge", "branch": branch, "pr": pr_num},
                    )
                except Exception:
                    pass  # fail-open
                return _ok(
                    f"Branch `{branch}` merged to main (PR #{pr_num}).",
                    pr=pr_num, branch=branch,
                )
            else:
                return _err(f"Merge failed ({merge_r.status_code}): {merge_r.text[:200]}")
    except Exception as e:
        return _err(f"Merge error: {e}")


async def _cmd_branches(args: str, channel: dict, agent: dict, db) -> dict:
    """List all active branches/sessions in this channel."""
    channel_id = channel["id"]
    rows = await (await db.execute(
        """SELECT s.contributor_id, s.branch_name, s.last_commit_sha, s.status, s.created_at,
               a.name as agent_name
          FROM channel_code_sessions s
          LEFT JOIN agents a ON a.id = s.contributor_id
         WHERE s.channel_id=? AND s.status='active'
         ORDER BY s.created_at DESC""",
        (channel_id,),
    )).fetchall()

    if not rows:
        return _ok("No active branches in this channel yet.", sessions=[])

    lines = [
        "**Active branches:**",
        "| Contributor | Branch | Last Commit |",
        "|---|---|---|",
    ]
    for r in rows:
        cname  = r["agent_name"] or str(r["contributor_id"])
        branch = r["branch_name"]
        sha    = (r["last_commit_sha"] or "--")[:8]
        lines.append(f"| {cname} | `{branch}` | `{sha}` |")

    sessions = [
        {
            "contributor": r["agent_name"] or r["contributor_id"],
            "branch":      r["branch_name"],
            "sha":         (r["last_commit_sha"] or "")[:8],
        }
        for r in rows
    ]
    return _ok("\n".join(lines), sessions=sessions)


# ── main dispatcher ─────────────────────────────────────────────────────────

_COMMAND_MAP = {
    "/run":      _cmd_run,
    "/pr":       _cmd_pr,
    "/scan":     _cmd_scan,
    "/review":   _cmd_review,
    "/merge":    _cmd_merge,
    "/branches": _cmd_branches,
}

_HELP = (
    "**Code channel commands:**\n"
    "- `/run <prompt>` -- generate code from a prompt and push to your branch\n"
    "- `/pr <title>` -- open a PR from your branch to main\n"
    "- `/scan` -- run a security scan on your branch\n"
    "- `/review [branch]` -- show last 5 commits on a branch\n"
    "- `/merge [branch]` -- merge a branch to main (admin / own branch after scan)\n"
    "- `/branches` -- list all active contributor branches\n"
)


async def handle(cmd: str, args: str, channel: dict, agent: dict, db) -> dict:
    """Main entry point called by channel_slash.dispatch for code channels."""
    cmd_lower = cmd.lower() if cmd.startswith("/") else f"/{cmd.lower()}"

    if cmd_lower == "/help":
        return _ok(_HELP)

    handler = _COMMAND_MAP.get(cmd_lower)
    if handler is None:
        return _ok(f"Unknown command `{cmd}` in code channel.\n\n{_HELP}")

    try:
        return await handler(args, channel, agent, db)
    except Exception as exc:
        logger.exception("code_commands.handle unhandled error for %s", cmd)
        return _err(f"Internal error running {cmd}: {exc}")
