#!/usr/bin/env python3
"""
Opencode Runner — standalone host-side HTTP service that wraps the `opencode`
coding agent (sst/opencode) as a Vantage slash-command backend.

This is the OSS-as-slash-command wrap for /Opencode (sibling of strix_runner.py).
Unlike Strix (a scanner), Opencode is an autonomous coding agent: given a task and
an optional repo, it edits the working tree. The runner clones (optional), runs
`opencode run` headlessly, and returns the agent output + the resulting git diff.

Contract:
  POST /run   {task, clone_url?, model?, agent?, apply?}  -> {"run_id": "..."}
  GET  /run/{run_id}   -> {"status": "queued"|"cloning"|"running"|"complete"|"error",
                            "output": "...", "diff": "...", "files_changed": [...],
                            "session_id": "...", "error": "..."}
  GET  /health         -> {"ok": true, "service": "opencode-runner", "opencode": "<version>"}
"""
import os, subprocess, threading, uuid, shutil, tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

OPENCODE_BIN = os.environ.get("OPENCODE_BIN", "/usr/local/bin/opencode")
MODEL_DEFAULT = os.environ.get("OPENCODE_MODEL", "deepseek/deepseek-chat")
RUN_DIR = os.environ.get("OPENCODE_RUN_DIR", "/tmp/opencode-runner")
RUN_TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "1800"))
CLONE_TIMEOUT = int(os.environ.get("OPENCODE_CLONE_TIMEOUT", "60"))

app = FastAPI(title="opencode-runner")
_runs: dict[str, dict] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    task: str
    clone_url: str | None = None
    model: str | None = None
    agent: str | None = None
    apply: bool = True  # if False, run in a scratch dir with no repo (pure generation)


def _set(run_id: str, **fields):
    with _lock:
        _runs[run_id].update(fields)


def _git(target: str, *args, timeout: int = 30):
    return subprocess.run(["git", "-C", target, *args],
                          capture_output=True, text=True, timeout=timeout)


def _execute(run_id: str, req: RunRequest):
    target = os.path.join(RUN_DIR, run_id)
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    is_repo = False
    try:
        if req.clone_url:
            _set(run_id, status="cloning")
            r = subprocess.run(["git", "clone", "--depth", "1", req.clone_url, target],
                               capture_output=True, text=True, timeout=CLONE_TIMEOUT)
            if r.returncode != 0:
                _set(run_id, status="error", error=f"clone failed: {r.stderr[:300]}")
                return
            is_repo = True
        else:
            # scratch workspace so `opencode run` has a git context for a diff
            _git(target, "init", "-q")
            is_repo = True

        _set(run_id, status="running")
        cmd = [OPENCODE_BIN, "run", req.task,
               "-m", req.model or MODEL_DEFAULT, "--format", "default"]
        if req.agent:
            cmd += ["--agent", req.agent]
        env = os.environ.copy()
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=RUN_TIMEOUT, cwd=target, env=env)
        output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")

        diff, files_changed = "", []
        if is_repo:
            _git(target, "add", "-A")
            d = _git(target, "diff", "--cached")
            diff = d.stdout
            names = _git(target, "diff", "--cached", "--name-only")
            files_changed = [f for f in names.stdout.splitlines() if f.strip()]

        status = "complete" if proc.returncode == 0 else "error"
        _set(run_id, status=status, output=output[:200000], diff=diff[:400000],
             files_changed=files_changed,
             error=("" if proc.returncode == 0 else f"opencode exit {proc.returncode}"))
    except subprocess.TimeoutExpired:
        _set(run_id, status="error", error=f"timed out after {RUN_TIMEOUT}s")
    except Exception as e:
        _set(run_id, status="error", error=str(e)[:400])


@app.get("/health")
async def health():
    try:
        v = subprocess.run([OPENCODE_BIN, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        v = "unknown"
    return {"ok": True, "service": "opencode-runner", "opencode": v, "model_default": MODEL_DEFAULT}


@app.post("/run")
async def start_run(req: RunRequest):
    if not req.task.strip():
        raise HTTPException(400, "task is required")
    run_id = uuid.uuid4().hex[:12]
    with _lock:
        _runs[run_id] = {"status": "queued", "output": "", "diff": "",
                         "files_changed": [], "error": ""}
    threading.Thread(target=_execute, args=(run_id, req), daemon=True).start()
    return {"run_id": run_id}


@app.get("/run/{run_id}")
async def run_status(run_id: str):
    with _lock:
        r = _runs.get(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return {"run_id": run_id, **r}


if __name__ == "__main__":
    os.makedirs(RUN_DIR, exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "9879")))
