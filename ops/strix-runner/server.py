"""
Strix runner — a small standalone service that runs real Strix
(github.com/usestrix/strix) security scans on behalf of Vantage.

Deliberately NOT part of the Vantage FastAPI app or Docker image: Strix needs
a live Docker daemon and the `strix` CLI, and Vantage's API container is
intentionally locked down (non-root, no Docker access). This runs directly on
the VPS host, where Docker + strix are already installed. Vantage's
backend/routers/code.py talks to it over plain HTTP (STRIX_RUNNER_URL).

Endpoints:
  GET  /health           — liveness check
  POST /run               {clone_url, owner, name} -> {run_id}
  GET  /run/{run_id}      -> {status, findings}

Findings format is a placeholder until someone runs `strix` for real and
inspects strix_runs/<run-name>/ by hand — see README.md. Until then this
reports the raw run directory and an empty findings list rather than
guessing a parser.
"""

import asyncio
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("strix-runner")

app = FastAPI(title="Strix Runner")

WORK_DIR = Path(os.environ.get("STRIX_RUNNER_WORK_DIR", "/tmp/strix-runner"))
STRIX_LLM = os.environ.get("STRIX_LLM", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
SCAN_TIMEOUT_SEC = int(os.environ.get("STRIX_SCAN_TIMEOUT_SEC", "1800"))

_runs: dict[str, dict] = {}


class RunRequest(BaseModel):
    clone_url: str
    owner: str
    name: str


@app.get("/health")
async def health():
    return {"status": "ok", "service": "strix-runner", "strix_configured": bool(STRIX_LLM and LLM_API_KEY)}


@app.post("/run")
async def start_run(req: RunRequest):
    if not STRIX_LLM or not LLM_API_KEY:
        raise HTTPException(503, "STRIX_LLM / LLM_API_KEY not configured on the runner")

    run_id = uuid.uuid4().hex[:16]
    _runs[run_id] = {"status": "running", "findings": [], "raw_output_dir": "", "started_at": time.time()}
    asyncio.create_task(_execute(run_id, req))
    return {"run_id": run_id}


@app.get("/run/{run_id}")
async def get_run(run_id: str):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run_id")
    return run


async def _execute(run_id: str, req: RunRequest) -> None:
    target = WORK_DIR / f"scan_{req.name}_{run_id}"
    run_dir = WORK_DIR / "strix_runs"
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        clone = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", req.clone_url, str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, clone_err = await asyncio.wait_for(clone.communicate(), timeout=60)
        if clone.returncode != 0:
            _runs[run_id].update(status="error", error=clone_err.decode(errors="ignore")[:2000])
            return

        proc = await asyncio.create_subprocess_exec(
            "strix", "-n", "-t", str(target), "--scan-mode", "quick",
            cwd=str(WORK_DIR),
            env={**os.environ, "STRIX_LLM": STRIX_LLM, "LLM_API_KEY": LLM_API_KEY},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, strix_err = await asyncio.wait_for(proc.communicate(), timeout=SCAN_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            proc.kill()
            _runs[run_id].update(status="error", error="strix scan timed out")
            return

        # TODO: strix_runs/<run-name>/ output format is undocumented — inspect
        # a real run's output directory before writing a findings parser here.
        # For now, report where the raw output landed and leave findings empty
        # rather than guessing a schema.
        _runs[run_id].update(
            status="complete" if proc.returncode == 0 else "error",
            raw_output_dir=str(run_dir),
            findings=[],
            completed_at=time.time(),
        )
        if proc.returncode != 0:
            _runs[run_id]["error"] = strix_err.decode(errors="ignore")[:2000]
    except Exception as exc:
        logger.exception("strix run %s failed", run_id)
        _runs[run_id].update(status="error", error=str(exc))
    finally:
        shutil.rmtree(target, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("STRIX_RUNNER_PORT", "9877")))
