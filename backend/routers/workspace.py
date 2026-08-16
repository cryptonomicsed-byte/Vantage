"""Agent workspaces: clone repositories, run commands, edit files.

This is what lets an agent actually do engineering work — pull a project down,
run its tests, change a file, run them again — rather than only talk about it.

Nothing here executes on the Vantage host. Every operation is proxied to the
code-sandbox container (ops/code-sandbox), which is non-root, capability-
dropped, memory/pid/cpu capped, and can only write to its workspace volume. If
that container is not running, these endpoints return 503 rather than falling
back to local execution — a fallback that quietly ran agent code on the host
would defeat the entire point.

Each agent gets its own directory under the sandbox workspace, so one agent
cannot read or clobber another's checkout. That isolation is enforced here (the
agent never supplies its own prefix) and again inside the sandbox, which
confines every path under its workspace root.

Because voice tool discovery is derived from FastAPI's route table, adding
these routes is all it takes to make them callable by voice — with the same
per-session allowlist gating everything else. `tag:workspace` selects them.
"""
import logging
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# Empty disables the feature, matching the "empty URL = no-op" convention the
# other optional sidecars use. Deliberately empty by default: this is the one
# service that runs arbitrary code, so it should be switched on knowingly.
CODE_SANDBOX_URL = os.environ.get("CODE_SANDBOX_URL", "")

_SANDBOX_TIMEOUT = 960.0  # must outlast the sandbox's own 900s command ceiling


def _agent_dir(agent: dict) -> str:
    """Per-agent workspace prefix. Derived from the agent id, never from
    caller input, so one agent's path cannot be steered into another's."""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", str(agent.get("name") or ""))[:40]
    return f"agent-{agent['id']}-{safe_name}"


def _scoped(agent: dict, path: Optional[str]) -> str:
    """Rebase a caller path under this agent's directory."""
    relative = (path or ".").lstrip("/")
    if relative in ("", "."):
        return _agent_dir(agent)
    return f"{_agent_dir(agent)}/{relative}"


async def _sandbox(endpoint: str, payload: dict) -> dict:
    if not CODE_SANDBOX_URL:
        raise HTTPException(
            503,
            "Code sandbox is not configured. Start it with "
            "`docker compose --profile code up -d` and set CODE_SANDBOX_URL "
            "(e.g. http://127.0.0.1:9880).",
        )
    try:
        async with httpx.AsyncClient(timeout=_SANDBOX_TIMEOUT) as client:
            response = await client.post(f"{CODE_SANDBOX_URL.rstrip('/')}{endpoint}", json=payload)
    except httpx.HTTPError as exc:
        logger.warning("code sandbox unreachable: %s", exc)
        raise HTTPException(503, f"Code sandbox is unavailable: {exc}")

    if response.status_code >= 400:
        try:
            detail = response.json().get("error", response.text)
        except ValueError:
            detail = response.text
        raise HTTPException(422, f"Sandbox rejected the request: {detail}")
    return response.json()


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.get("/status")
async def workspace_status(agent: dict = Depends(get_agent)):
    """Whether code execution is available, and where this agent's files live."""
    if not CODE_SANDBOX_URL:
        return {"available": False, "reason": "CODE_SANDBOX_URL is not set"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CODE_SANDBOX_URL.rstrip('/')}/health")
        healthy = response.status_code == 200
    except httpx.HTTPError as exc:
        return {"available": False, "reason": f"sandbox unreachable: {exc}"}
    return {"available": healthy, "workspace": _agent_dir(agent)}


@router.post("/clone")
async def clone_repository(request: Request, agent: dict = Depends(get_agent)):
    """Clone a public https repository into this agent's workspace.

    Shallow by default — full history is rarely what an agent needs and is much
    slower on a large repo. Pass full_history=true when it is.
    """
    body = await _body(request)
    repo_url = str(body.get("repo_url") or "").strip()
    if not repo_url:
        raise HTTPException(422, "repo_url is required")

    result = await _sandbox("/clone", {
        "repo_url": repo_url,
        "dir": _scoped(agent, body.get("dir") or _default_dir(repo_url)),
        "full_history": bool(body.get("full_history")),
        "timeout_ms": body.get("timeout_ms"),
    })
    logger.info("agent_id=%s cloned %s", agent["id"], repo_url)
    return _relative(agent, result)


def _default_dir(repo_url: str) -> str:
    return re.sub(r"\.git$", "", repo_url.rstrip("/")).split("/")[-1] or "repo"


@router.post("/exec")
async def execute_command(request: Request, agent: dict = Depends(get_agent)):
    """Run a shell command inside this agent's workspace.

    Returns exit code, stdout, stderr and duration — including for a failure,
    because a failing build is information the agent needs, not an error to
    hide. A command that exceeds its timeout is killed by process group and
    comes back with exit_code 124 and timed_out=true.
    """
    body = await _body(request)
    command = str(body.get("command") or "").strip()
    if not command:
        raise HTTPException(422, "command is required")

    result = await _sandbox("/exec", {
        "command": command,
        "cwd": _scoped(agent, body.get("cwd")),
        "timeout_ms": body.get("timeout_ms"),
        "env": body.get("env") if isinstance(body.get("env"), dict) else None,
    })
    logger.info("agent_id=%s exec exit=%s: %.120s", agent["id"], result.get("exit_code"), command)
    return result


@router.post("/read")
async def read_file(request: Request, agent: dict = Depends(get_agent)):
    body = await _body(request)
    if not body.get("path"):
        raise HTTPException(422, "path is required")
    result = await _sandbox("/read", {"path": _scoped(agent, body["path"])})
    return _relative(agent, result)


@router.post("/write")
async def write_file(request: Request, agent: dict = Depends(get_agent)):
    body = await _body(request)
    if not body.get("path"):
        raise HTTPException(422, "path is required")
    result = await _sandbox("/write", {
        "path": _scoped(agent, body["path"]),
        "content": body.get("content", ""),
    })
    return _relative(agent, result)


@router.post("/list")
async def list_files(request: Request, agent: dict = Depends(get_agent)):
    body = await _body(request)
    result = await _sandbox("/list", {"path": _scoped(agent, body.get("path"))})
    return _relative(agent, result)


@router.post("/remove")
async def remove_path(request: Request, agent: dict = Depends(get_agent)):
    """Delete a file or directory from this agent's workspace."""
    body = await _body(request)
    if not body.get("path"):
        raise HTTPException(422, "path is required")
    target = str(body["path"]).strip()
    if target in (".", "/", ""):
        # Scoping would make this the agent's own root, which is recoverable,
        # but silently wiping a whole workspace on a vague argument is not what
        # the caller meant.
        raise HTTPException(422, "refusing to remove the whole workspace; name a path")
    result = await _sandbox("/remove", {"path": _scoped(agent, target)})
    return _relative(agent, result)


def _relative(agent: dict, result: dict) -> dict:
    """Strip the agent's directory prefix off anything echoed back.

    The prefix is an implementation detail of isolation; leaking it into
    responses invites the model to start constructing paths with it, which is
    exactly the input we do not want to trust.
    """
    prefix = _agent_dir(agent) + "/"
    out = dict(result)
    for key in ("path", "dir"):
        value = out.get(key)
        if isinstance(value, str):
            if value == _agent_dir(agent):
                out[key] = "."
            elif value.startswith(prefix):
                out[key] = value[len(prefix):]
    return out
