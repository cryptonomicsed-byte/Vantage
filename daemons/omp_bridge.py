#!/usr/bin/env python3
"""Bridge: oh-my-pi coding agent → Vantage code pipeline

oh-my-pi itself now lives on Contabo (10.88.0.2) -- moved off Hostinger
to relieve disk pressure. The coding-agent CLI is invoked remotely over
SSH through the WireGuard tunnel instead of a local subprocess call, and
its actual work directory now lives on Contabo too (it produces real
files there, not on this box)."""
import os, json, shlex, subprocess, urllib.request
from datetime import datetime

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
CONTABO_HOST = os.environ.get("CONTABO_TUNNEL_HOST", "10.88.0.2")
# oh-my-pi's coding-agent needs a real model API key on the remote side;
# SSH exec doesn't inherit Contabo's systemd-scoped env vars, so this is
# passed through explicitly rather than assumed ambient.
OMP_DEEPSEEK_API_KEY = os.environ.get("OMP_DEEPSEEK_API_KEY", "")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new"]

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY, "User-Agent": "curl/8.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def run_omp(prompt: str, workdir: str = "/tmp/omp-work"):
    """Run oh-my-pi's real coding agent on Contabo over SSH, push results to Gitea + Vantage.

    --prompt is not a real flag (found via live testing) -- the message is
    positional, and --print/--model/--no-pty are required for a genuine
    non-interactive, non-PTY run. --model must be "deepseek/<model-id>"
    (provider/model, not a bare model name) or the CLI's fuzzy matcher
    resolves to the wrong provider (observed: silently matched "kilo").
    """
    remote_cmd = (
        f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)} && "
        f"export PATH=$HOME/.bun/bin:$PATH && "
        f"export DEEPSEEK_API_KEY={shlex.quote(OMP_DEEPSEEK_API_KEY)} && "
        f"bun /opt/oh-my-pi/packages/coding-agent/src/cli.ts {shlex.quote(prompt)} "
        f"--print --model deepseek/deepseek-v4-flash --no-pty"
    )
    result = subprocess.run(
        ["ssh", *SSH_OPTS, f"root@{CONTABO_HOST}", remote_cmd],
        capture_output=True, text=True, timeout=300,
    )
    return result.stdout, result.stderr

def ingest_to_vantage(prompt: str, output: str):
    """Post oh-my-pi results to Vantage code pipeline."""
    return vantage_post("/api/agents/posts/text", {
        "title": f"omp: {prompt[:60]}",
        "content": output[:2000],
        "content_type": "text",
        "tags": ["omp", "code", "agent"],
        "status": "published"
    })

if __name__ == "__main__":
    import sys
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Write a Python hello world"
    print(f"[omp-bridge] Running on {CONTABO_HOST}: {prompt[:80]}")
    stdout, stderr = run_omp(prompt)
    result = ingest_to_vantage(prompt, stdout)
    print(f"[omp-bridge] Posted broadcast #{result.get('broadcast_id', '?')}")
