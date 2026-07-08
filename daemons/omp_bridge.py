#!/usr/bin/env python3
"""Bridge: oh-my-pi coding agent → Vantage code pipeline"""
import os, json, subprocess, urllib.request
from datetime import datetime

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY, "User-Agent": "curl/8.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def run_omp(prompt: str, workdir: str = "/tmp/omp-work"):
    """Run oh-my-pi coding agent on a prompt, push results to Gitea + Vantage."""
    os.makedirs(workdir, exist_ok=True)
    result = subprocess.run(
        ["bun", "/opt/ares/oh-my-pi/packages/coding-agent/src/cli.ts", "--prompt", prompt],
        capture_output=True, text=True, timeout=300, cwd=workdir
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
    print(f"[omp-bridge] Running: {prompt[:80]}")
    stdout, stderr = run_omp(prompt)
    result = ingest_to_vantage(prompt, stdout)
    print(f"[omp-bridge] Posted broadcast #{result.get('broadcast_id', '?')}")
