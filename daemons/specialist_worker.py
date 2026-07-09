#!/usr/bin/env python3
"""Specialist Worker — the execution layer for agency-agents personas.

One lightweight process (not a daemon per agent). It claims open Job Conductor
tasks, loads the matching agency-agents persona (a markdown system prompt),
runs the inference through OmniRoute (the free AI gateway), and reports the
result back — submitting the task and, for security work, writing a structured
row into security_scans so it surfaces in the ARES SENTINEL Security tab.

Model: Vantage-hosted inference via OmniRoute (free-token gateway), so agents
who can't BYOK still get worked. Agents who bring their own keys/models run
their own flows and don't need this.

A task is claimed only if its required_capability maps to a known persona, so
this never steals tasks meant for humans or BYOK agents.

Run: python3 specialist_worker.py [--once] [--interval 20]
"""

import os
import re
import sys
import json
import time
import glob
import logging
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s specialist %(message)s")
log = logging.getLogger("specialist")

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://localhost:8300")
OMNIROUTE_MODEL = os.environ.get("OMNIROUTE_MODEL", "auto/best-coding")
AGENCY_DIR = os.environ.get("AGENCY_DIR", "/opt/ares/agency-agents")
KEY_FILE = os.path.expanduser(os.environ.get("SPECIALIST_KEY_FILE", "~/.specialist_worker_key"))
POLL_INTERVAL = int(os.environ.get("SPECIALIST_INTERVAL", "20"))
# Which job_types this worker will touch (others left for their own runners).
HANDLED_JOB_TYPES = {"security", "generic", "code"}
SECURITY_HINT = re.compile(r"security|pentest|vuln|threat|incident|secops|appsec|audit", re.I)


# ── HTTP helpers ────────────────────────────────────────────────────────────
def _req(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "User-Agent": "specialist-worker/1.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def vantage(method, path, body=None):
    return _req(method, f"{VANTAGE_URL}{path}", body, {"X-Agent-Key": AGENT_KEY})


# ── Agent identity ──────────────────────────────────────────────────────────
def ensure_agent_key() -> str:
    if os.path.exists(KEY_FILE):
        k = open(KEY_FILE).read().strip()
        if k:
            return k
    name = f"SpecialistWorker-{os.getpid()}"
    try:
        r = _req("POST", f"{VANTAGE_URL}/api/agents/register",
                 {"name": name, "bio": "#security #specialist agency-agents execution worker"})
        key = r["api_key"]
        Path(KEY_FILE).write_text(key)
        os.chmod(KEY_FILE, 0o600)
        log.info("registered worker agent %s", name)
        return key
    except Exception as e:
        log.error("could not register worker agent: %s", e)
        sys.exit(1)


# ── Persona index (agency-agents/*.md) ──────────────────────────────────────
def build_persona_index() -> dict:
    """slug → filepath, e.g. 'security-penetration-tester' → .../security/....md"""
    idx = {}
    for path in glob.glob(os.path.join(AGENCY_DIR, "**", "*.md"), recursive=True):
        base = os.path.basename(path)
        if base.upper() in ("README.MD", "CONTRIBUTING.MD", "SECURITY.MD", "SUPPORT.MD"):
            continue
        slug = base[:-3].lower()
        idx[slug] = path
    return idx


def match_persona(capability: str, index: dict) -> str | None:
    """Map a task's required_capability to a persona slug."""
    if not capability:
        return None
    cap = capability.strip().lower().replace(" ", "-").replace("_", "-")
    if cap in index:
        return cap
    # e.g. capability 'penetration-tester' → 'security-penetration-tester'
    for slug in index:
        if slug.endswith(cap) or cap in slug:
            return slug
    return None


def load_persona(path: str) -> str:
    """Return the persona body with YAML frontmatter stripped."""
    text = Path(path).read_text(errors="ignore")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text.strip()


# ── Inference via OmniRoute ─────────────────────────────────────────────────
# Free-tier upstreams flake (503/429). Try the preferred model, then fall back.
_MODEL_CHAIN = [OMNIROUTE_MODEL, "auto/fast", "auto/best-coding"]


def run_inference(system_prompt: str, user_prompt: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt[:8000]},
        {"role": "user", "content": user_prompt[:6000]},
    ]
    last_err = None
    seen = []
    for model in _MODEL_CHAIN:
        if model in seen:
            continue
        seen.append(model)
        for attempt in range(2):
            try:
                r = _req("POST", f"{OMNIROUTE_URL}/v1/chat/completions", {
                    "model": model, "messages": messages, "stream": False,
                    "temperature": 0.3, "max_tokens": 1500,
                }, timeout=120)
                return r["choices"][0]["message"]["content"].strip()
            except Exception as e:
                last_err = e
                time.sleep(2)
    raise RuntimeError(f"all models failed: {last_err}")


# ── Task processing ─────────────────────────────────────────────────────────
def process_task(job: dict, task: dict, persona_slug: str, persona_body: str):
    job_id, task_id = job["id"], task["id"]
    log.info("claiming task %s/%s as '%s'", job_id, task_id, persona_slug)
    try:
        vantage("POST", f"/api/jobs/{job_id}/tasks/{task_id}/claim")
    except Exception as e:
        log.info("claim failed (someone else got it?): %s", e)
        return

    user_prompt = (
        f"Job: {job.get('title','')}\n{job.get('description','')}\n\n"
        f"Your task: {task.get('title','')}\n{task.get('description','')}\n\n"
        "Do the task. Be concrete and actionable. If this is a security review, "
        "list concrete findings (severity + what + where) or state CLEAN."
    )
    try:
        result = run_inference(persona_body, user_prompt)
    except Exception as e:
        log.error("inference failed: %s", e)
        return

    # Security work → structured security_scans row (SENTINEL Security tab).
    is_sec = job.get("job_type") == "security" or SECURITY_HINT.search(persona_slug)
    if is_sec:
        vulnerable = bool(re.search(r"\b(vulnerab|critical|high\b|exploit|CVE-)", result, re.I)) \
            and not re.search(r"\bclean\b", result[:200], re.I)
        findings = [ln.strip("-• ").strip() for ln in result.splitlines() if ln.strip()][:20]
        try:
            vantage("POST", "/api/security/scan-result", {
                "tool": persona_slug,
                "target": task.get("title", "")[:120],
                "vulnerable": vulnerable,
                "findings": findings,
            })
        except Exception as e:
            log.warning("scan-result post failed: %s", e)

    # Submit the task result.
    try:
        vantage("POST", f"/api/jobs/{job_id}/tasks/{task_id}/submit",
                {"result_description": result[:2000]})
        log.info("submitted task %s/%s (%d chars)", job_id, task_id, len(result))
    except Exception as e:
        log.warning("submit failed: %s", e)


def sweep_once(index: dict) -> int:
    """Claim+run one eligible task per pass. Returns count processed."""
    try:
        jobs = vantage("GET", "/api/jobs")
    except Exception as e:
        log.debug("jobs list failed: %s", e)
        return 0
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    done = 0
    for j in jobs:
        if j.get("status") not in (None, "open", "in_progress"):
            continue
        if j.get("job_type") not in HANDLED_JOB_TYPES:
            continue
        try:
            full = vantage("GET", f"/api/jobs/{j['id']}")
        except Exception:
            continue
        for t in full.get("tasks", []):
            if t.get("status") != "open":
                continue
            slug = match_persona(t.get("required_capability", ""), index)
            if not slug:
                continue
            process_task(full, t, slug, load_persona(index[slug]))
            done += 1
            if done >= 3:  # bounded per pass
                return done
    return done


def main():
    global AGENT_KEY
    once = "--once" in sys.argv
    AGENT_KEY = ensure_agent_key()
    index = build_persona_index()
    log.info("loaded %d personas from %s", len(index), AGENCY_DIR)
    if not index:
        log.warning("no personas found — is AGENCY_DIR correct? (%s)", AGENCY_DIR)
    while True:
        n = sweep_once(index)
        if n:
            log.info("processed %d task(s)", n)
        if once:
            break
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    AGENT_KEY = ""
    main()
