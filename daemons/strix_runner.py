#!/usr/bin/env python3
"""
Strix Runner — standalone host-side HTTP service that Vantage's
POST /api/code/repo/{owner}/{name}/scan?engine=strix dispatches to.

Contract (matches backend/routers/code.py):
  POST /run   {clone_url, owner, name}  -> {"run_id": "..."}
  GET  /run/{run_id}                    -> {"status": "running"|"complete"|"error",
                                              "findings": [...]}
"""
import json, os, subprocess, threading, time, uuid, shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

STRIX_BIN = "/opt/ares/venv/bin/strix"
STRIX_CWD = "/opt/ares/strix"
SCAN_DIR = "/tmp/strix-runner"
STRIX_LLM = os.environ.get("STRIX_LLM", "deepseek/deepseek-chat")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
SCAN_MODE = os.environ.get("STRIX_RUNNER_MODE", "quick")  # quick|standard|deep

app = FastAPI(title="strix-runner")
_runs: dict[str, dict] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    clone_url: str
    owner: str
    name: str


def _set(run_id: str, **fields):
    with _lock:
        _runs[run_id].update(fields)


def _execute(run_id: str, clone_url: str, owner: str, name: str):
    target = os.path.join(SCAN_DIR, run_id)
    try:
        _set(run_id, status="cloning")
        shutil.rmtree(target, ignore_errors=True)
        r = subprocess.run(["git", "clone", "--depth", "1", clone_url, target],
                            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            _set(run_id, status="error", findings=[], error=f"clone failed: {r.stderr[:300]}")
            return

        _set(run_id, status="running")
        env = os.environ.copy()
        env["STRIX_LLM"] = STRIX_LLM
        env["LLM_API_KEY"] = LLM_API_KEY

        proc = subprocess.run(
            [STRIX_BIN, "-n", "--target", target, "-m", SCAN_MODE],
            capture_output=True, text=True, timeout=1800, env=env, cwd=STRIX_CWD,
        )

        findings = []
        runs_dir = Path(STRIX_CWD) / "strix_runs"
        if runs_dir.exists():
            candidates = sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for run in candidates[:1]:
                report = run / "report.json"
                if report.exists():
                    findings = json.loads(report.read_text()).get("findings", [])

        if proc.returncode != 0 and not findings:
            _set(run_id, status="error", findings=[],
                 error=f"strix exit {proc.returncode}: {(proc.stdout + proc.stderr)[-500:]}")
            return

        _set(run_id, status="complete", findings=findings)
    except subprocess.TimeoutExpired:
        _set(run_id, status="error", findings=[], error="strix run timed out")
    except Exception as e:
        _set(run_id, status="error", findings=[], error=str(e))
    finally:
        shutil.rmtree(target, ignore_errors=True)


@app.get("/health")
async def health():
    return {"status": "ok", "llm_configured": bool(LLM_API_KEY), "mode": SCAN_MODE}


@app.post("/run")
async def start_run(req: RunRequest):
    run_id = uuid.uuid4().hex[:16]
    with _lock:
        _runs[run_id] = {"status": "pending", "findings": [], "owner": req.owner,
                          "name": req.name, "started_at": time.time()}
    t = threading.Thread(target=_execute, args=(run_id, req.clone_url, req.owner, req.name), daemon=True)
    t.start()
    return {"run_id": run_id}


@app.get("/run/{run_id}")
async def run_status(run_id: str):
    with _lock:
        row = _runs.get(run_id)
    if not row:
        raise HTTPException(404, "run not found")
    return {"run_id": run_id, **row}


if __name__ == "__main__":
    os.makedirs(SCAN_DIR, exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "9877")))
