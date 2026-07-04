"""Code Collaboration API v2 — Agent-first endpoints for the full pipeline.

Every action an agent needs:
  GET  /api/code/overview          — All repos with status
  GET  /api/code/repo/{owner}/{name} — Single repo detail
  POST /api/code/repo/create        — Create a new repo
  POST /api/code/repo/{owner}/{name}/push — Push file content
  POST /api/code/repo/{owner}/{name}/scan — Trigger a Strix (or fast regex) security scan
  GET  /api/code/repo/{owner}/{name}/scan/{scan_id} — Poll an async Strix scan
  GET  /api/code/repo/{owner}/{name}/scan-results — Latest scan
  POST /api/code/repo/{owner}/{name}/memory — Ingest content into supermemory
  GET  /api/code/activity           — Recent activity feed
  GET  /api/code/stats              — Aggregate stats
  POST /api/code/repo/{owner}/{name}/pr — Open a PR
  POST /api/code/search             — Search code across repos

Every mutating endpoint requires X-Agent-Key (Depends(get_agent)) — these push
code and trigger scans on an agent's behalf, so they're authenticated like the
rest of Vantage's write endpoints, and become real authenticated MCP tools
once wired through the /mcp mount (see backend/mcp_server.py).
"""

import logging, httpx, json, os, time, base64, subprocess, shutil, re
from datetime import datetime, timezone
from typing import Literal, Optional
from fastapi import APIRouter, Depends, Query, Body, HTTPException
from pydantic import BaseModel

import aiosqlite

from ..config import settings
from ..db import DB_PATH
from ..deps import get_agent
from ..supermemory_client import SupermemoryClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/code", tags=["code"])

GITEA_URL = settings.GITEA_URL or "http://2.25.70.156:3001"
GITEA_API = f"{GITEA_URL}/api/v1"
# No hardcoded fallback — must come from VANTAGE_GITEA_TOKEN or GITEA_TOKEN env vars.
# A previous version of this file shipped a live-looking default token here;
# treat that value as compromised and rotate it on the Gitea instance.
GITEA_TOKEN = settings.GITEA_TOKEN or os.environ.get("GITEA_TOKEN", "")
SCAN_DIR = "/tmp/vantage_code_scan"
VANTAGE_URL = "http://127.0.0.1:8001"

_headers = {"Accept": "application/json", "Authorization": f"token {GITEA_TOKEN}"}
_cache = {"data": None, "ts": 0}
_activity_feed: list[dict] = []
_activity_max = 100
_supermemory = SupermemoryClient(settings.SUPERMEMORY_URL, settings.SUPERMEMORY_API_KEY)

# ── Models ──────────────────────────────────────────────────────────────

class CreateRepoRequest(BaseModel):
    name: str
    description: str = ""
    private: bool = False

class PushFileRequest(BaseModel):
    path: str
    content: str
    message: str = "Update via Vantage API"
    branch: str = "main"

class CreatePRRequest(BaseModel):
    title: str
    body: str = ""
    head: str
    base: str = "main"

class SearchRequest(BaseModel):
    query: str
    repo: Optional[str] = None

class MemoryIngestRequest(BaseModel):
    content: str
    metadata: Optional[dict] = None
    custom_id: str = ""

# ── Helpers ─────────────────────────────────────────────────────────────

def _log_activity(action: str, repo: str, detail: str = "", agent: str = "vantage-agent"):
    """Record an activity event."""
    _activity_feed.append({
        "action": action,
        "repo": repo,
        "detail": detail,
        "agent": agent,
        "ts": int(time.time()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    if len(_activity_feed) > _activity_max:
        _activity_feed.pop(0)


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/overview")
async def code_overview():
    """Get all repos with Strix scan status, commits, PRs, branches."""
    import time as _time
    now = int(_time.time())
    if _cache["data"] and (now - _cache["ts"]) < 15:
        return _cache["data"]

    repos = []
    async with httpx.AsyncClient(timeout=10) as cl:
        try:
            r = await cl.get(f"{GITEA_API}/repos/search?limit=30", headers=_headers)
            repo_data = r.json().get("data", []) if r.status_code == 200 else []
        except:
            repo_data = []

        for repo in repo_data:
            full_name = repo.get("full_name", "")
            owner, name = full_name.split("/") if "/" in full_name else ("", full_name)
            info = {
                "name": name, "full_name": full_name, "owner": owner,
                "description": repo.get("description", ""),
                "updated_at": repo.get("updated_at", ""),
                "html_url": f"{GITEA_URL}/{full_name}",
                "clone_url": repo.get("clone_url", ""),
                "ssh_url": repo.get("ssh_url", ""),
                "stars": repo.get("stars_count", 0),
                "forks": repo.get("forks_count", 0),
                "open_issues": repo.get("open_issues_count", 0),
                "default_branch": repo.get("default_branch", "main"),
                "size_kb": repo.get("size", 0),
                "language": repo.get("language", ""),
                "branches": [],
                "recent_commits": [],
                "open_prs": [],
                "webhooks": [],
                "strix_scan_status": "unknown",
                "api_endpoints": {
                    "detail": f"/api/code/repo/{owner}/{name}",
                    "scan": f"/api/code/repo/{owner}/{name}/scan",
                    "scan_results": f"/api/code/repo/{owner}/{name}/scan-results",
                    "push": f"/api/code/repo/{owner}/{name}/push",
                    "pr": f"/api/code/repo/{owner}/{name}/pr",
                },
            }

            # Branches
            try:
                br = await cl.get(f"{GITEA_API}/repos/{full_name}/branches", headers=_headers)
                if br.status_code == 200:
                    info["branches"] = [b.get("name") for b in br.json()[:8]]
            except: pass

            # Commits
            try:
                branch = info["default_branch"]
                cm = await cl.get(f"{GITEA_API}/repos/{full_name}/commits?limit=3&sha={branch}", headers=_headers)
                if cm.status_code == 200:
                    for c in cm.json()[:3]:
                        commit_data = c.get("commit", {})
                        info["recent_commits"].append({
                            "sha": c.get("sha", "")[:8],
                            "message": (commit_data.get("message", "") or "").split("\n")[0][:80],
                            "author": c.get("author", {}).get("login", c.get("committer", {}).get("login", "unknown")),
                            "date": commit_data.get("committer", {}).get("date", ""),
                        })
            except: pass

            # Strix (security scan) webhooks
            try:
                hooks = await cl.get(f"{GITEA_API}/repos/{full_name}/hooks", headers=_headers)
                if hooks.status_code == 200:
                    for h in hooks.json():
                        config = h.get("config", {})
                        if "9876" in config.get("url", ""):
                            info["webhooks"].append({
                                "id": h.get("id"),
                                "type": "strix_scan",
                                "active": h.get("active", False),
                                "url": config.get("url", ""),
                            })
            except: pass

            info["strix_scan_status"] = "active" if info["webhooks"] else "no_hook"
            repos.append(info)

    result = {
        "repos": repos, "total": len(repos),
        "with_hooks": sum(1 for r in repos if r["webhooks"]),
        "open_prs_total": sum(len(r.get("open_prs", [])) for r in repos),
        "total_commits_recent": sum(len(r.get("recent_commits", [])) for r in repos),
        "total_size_kb": sum(r.get("size_kb", 0) for r in repos),
        "languages": list(set(r.get("language", "") for r in repos if r.get("language"))),
        "timestamp": now,
    }
    _cache["data"] = result
    _cache["ts"] = now
    return result


@router.get("/repo/{owner}/{name}")
async def repo_detail(owner: str, name: str):
    """Get detailed info for a single repo."""
    full_name = f"{owner}/{name}"
    async with httpx.AsyncClient(timeout=10) as cl:
        try:
            r = await cl.get(f"{GITEA_API}/repos/{full_name}", headers=_headers)
            if r.status_code == 200:
                return r.json()
        except: pass
    raise HTTPException(404, "Repo not found")

@router.get("/repo/{owner}/{name}/detail")
async def repo_detail_full(owner: str, name: str):
    """Comprehensive repo profile — Strix, Gitea stats, collaborators, commits."""
    full_name = f"{owner}/{name}"
    import time as _time, json as _json, os as _os

    result = {
        "name": name, "full_name": full_name, "owner": owner,
        "description": "", "html_url": f"{GITEA_URL}/{full_name}",
        "clone_url": "", "ssh_url": "",
        "default_branch": "main", "language": "", "size_kb": 0,
        "stars": 0, "forks": 0, "open_issues": 0, "watchers": 0,
        "created_at": "", "updated_at": "",
        "branches": [], "collaborators": [],
        "recent_commits": [], "open_prs": [],
        "strix_webhooks": [], "strix_active": False,
        "scan_results": None,
        "api_endpoints": {
            "scan": f"/api/code/repo/{owner}/{name}/scan",
            "push": f"/api/code/repo/{owner}/{name}/push",
            "pr": f"/api/code/repo/{owner}/{name}/pr",
            "detail": f"/api/code/repo/{owner}/{name}/detail",
        },
    }

    async with httpx.AsyncClient(timeout=15) as cl:
        # Gitea repo info
        try:
            r = await cl.get(f"{GITEA_API}/repos/{full_name}", headers=_headers)
            if r.status_code == 200:
                info = r.json()
                result.update({
                    "description": info.get("description", ""),
                    "clone_url": info.get("clone_url", ""),
                    "ssh_url": info.get("ssh_url", ""),
                    "default_branch": info.get("default_branch", "main"),
                    "language": info.get("language", ""),
                    "size_kb": info.get("size", 0),
                    "stars": info.get("stars_count", 0),
                    "forks": info.get("forks_count", 0),
                    "open_issues": info.get("open_issues_count", 0),
                    "watchers": info.get("watchers_count", 0),
                    "created_at": info.get("created_at", ""),
                    "updated_at": info.get("updated_at", ""),
                })
        except: pass

        # Branches
        try:
            br = await cl.get(f"{GITEA_API}/repos/{full_name}/branches", headers=_headers)
            if br.status_code == 200:
                result["branches"] = [b.get("name") for b in br.json()[:10]]
        except: pass

        # Collaborators (teams with access)
        try:
            col = await cl.get(f"{GITEA_API}/repos/{full_name}/collaborators", headers=_headers)
            if col.status_code == 200:
                result["collaborators"] = [c.get("login", c.get("username", "?")) for c in col.json()[:20]]
        except: pass

        # Commits
        try:
            branch = result["default_branch"]
            cm = await cl.get(f"{GITEA_API}/repos/{full_name}/commits?limit=5&sha={branch}", headers=_headers)
            if cm.status_code == 200:
                for c in cm.json()[:5]:
                    commit_data = c.get("commit", {})
                    result["recent_commits"].append({
                        "sha": c.get("sha", "")[:8],
                        "message": (commit_data.get("message", "") or "").split("\n")[0][:100],
                        "author": c.get("author", {}).get("login", c.get("committer", {}).get("login", "unknown")),
                        "date": commit_data.get("committer", {}).get("date", ""),
                    })
        except: pass

        # PRs
        try:
            pr = await cl.get(f"{GITEA_API}/repos/{full_name}/pulls?state=open&limit=5", headers=_headers)
            if pr.status_code == 200:
                for p in pr.json()[:5]:
                    result["open_prs"].append({
                        "number": p.get("number"),
                        "title": p.get("title", ""),
                        "author": p.get("user", {}).get("login", "unknown"),
                        "created_at": p.get("created_at", ""),
                    })
        except: pass

        # Strix (security scan) webhooks
        try:
            hooks = await cl.get(f"{GITEA_API}/repos/{full_name}/hooks", headers=_headers)
            if hooks.status_code == 200:
                for h in hooks.json():
                    config = h.get("config", {})
                    result["strix_webhooks"].append({
                        "id": h.get("id"), "type": h.get("type"),
                        "active": h.get("active", False),
                        "url": config.get("url", ""),
                        "events": h.get("events", []),
                    })
            result["strix_active"] = len(result["strix_webhooks"]) > 0
        except: pass

    return result



@router.post("/repo/create")
async def create_repo(req: CreateRepoRequest, agent: dict = Depends(get_agent)):
    """Create a new Git repo. Agent-friendly endpoint."""
    payload = {
        "name": req.name,
        "description": req.description,
        "private": req.private,
        "auto_init": True,
    }
    async with httpx.AsyncClient(timeout=10) as cl:
        try:
            r = await cl.post(f"{GITEA_API}/user/repos", json=payload, headers=_headers)
            if r.status_code in (200, 201):
                data = r.json()
                _log_activity("repo_created", f"ares-bot/{req.name}", req.description or "", agent=agent["name"])
                # Auto-register the security-scan webhook (secret is a shared value
                # with the external :9876 receiver — do not change without updating
                # that receiver too).
                webhook_payload = {
                    "type": "gitea",
                    "config": {"url": "http://localhost:9876/", "content_type": "json", "secret": "vantage-stix-webhook-2026"},
                    "events": ["push"], "active": True,
                }
                await cl.post(f"{GITEA_API}/repos/ares-bot/{req.name}/hooks", json=webhook_payload, headers=_headers)
                return {"status": "created", "repo": data.get("full_name", req.name),
                        "html_url": f"{GITEA_URL}/ares-bot/{req.name}",
                        "strix_hook": "auto-registered"}
        except Exception as e:
            raise HTTPException(500, str(e))
    raise HTTPException(500, "Gitea unavailable")


@router.post("/repo/{owner}/{name}/push")
async def push_file(owner: str, name: str, req: PushFileRequest, agent: dict = Depends(get_agent)):
    """Push a file to a repo. Agent can write code directly."""
    full_name = f"{owner}/{name}"
    target = os.path.join(SCAN_DIR, f"push_{name}_{int(time.time())}")
    os.makedirs(SCAN_DIR, exist_ok=True)

    try:
        # Clone
        clone_url = f"{GITEA_URL}/{full_name}.git".replace("http://", f"http://{GITEA_TOKEN}@")
        subprocess.run(["git", "clone", "--depth", "1", clone_url, target], capture_output=True, timeout=30)

        # Write file
        filepath = os.path.join(target, req.path)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(req.content)

        # Commit and push
        subprocess.run(["git", "-C", target, "add", req.path], capture_output=True)
        subprocess.run(["git", "-C", target, "config", "user.email", "agent@vantage.local"], capture_output=True)
        subprocess.run(["git", "-C", target, "config", "user.name", "Vantage Agent"], capture_output=True)
        subprocess.run(["git", "-C", target, "commit", "-m", req.message], capture_output=True)
        result = subprocess.run(["git", "-C", target, "push", "origin", req.branch], capture_output=True, timeout=30)

        _log_activity("push", full_name, f"{req.path}: {req.message[:50]}", agent=agent["name"])

        return {
            "status": "pushed",
            "repo": full_name,
            "path": req.path,
            "branch": req.branch,
            "message": req.message,
            "push_result": "ok" if result.returncode == 0 else "error",
        }
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        shutil.rmtree(target, ignore_errors=True)


async def _regex_scan(owner: str, name: str, agent_name: str) -> dict:
    """Fast, synchronous secret/vuln pattern scan — the existing default engine."""
    full_name = f"{owner}/{name}"
    clone_url = f"{GITEA_URL}/{full_name}.git".replace("http://", f"http://{GITEA_TOKEN}@")
    target = os.path.join(SCAN_DIR, f"scan_{name}")
    os.makedirs(SCAN_DIR, exist_ok=True)

    findings = []
    try:
        if os.path.exists(target):
            shutil.rmtree(target)
        subprocess.run(["git", "clone", "--depth", "1", clone_url, target], capture_output=True, timeout=30)

        # Scan all files
        files_scanned = 0
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', 'venv', '__pycache__')]
            for f in files:
                filepath = os.path.join(root, f)
                try:
                    with open(filepath, errors='ignore') as fh:
                        content = fh.read()
                except:
                    continue
                files_scanned += 1

                # Quick scan for secrets and vulns
                for line_num, line in enumerate(content.split('\n'), 1):
                    for pattern, vuln_id, severity in [
                        (r'(?:api[_-]?key|secret|token|password)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', 'HARDCODED_SECRET', 0.95),
                        (r'-----BEGIN.*PRIVATE KEY-----', 'PRIVATE_KEY', 0.99),
                        (r'(?:mnemonic|seed[_-]?phrase)\s*[=:]\s*["\'][a-z]+(?: [a-z]+){11,}', 'MNEMONIC', 0.99),
                        (r'(?:eval|exec)\s*\(', 'UNSAFE_EXEC', 0.80),
                        (r'\.execute\s*\(\s*f["\']', 'SQL_INJECTION', 0.82),
                        (r'verify\s*=\s*False', 'SSL_VERIFY_OFF', 0.78),
                    ]:
                        if re.search(pattern, line, re.IGNORECASE):
                            findings.append({
                                "file": os.path.relpath(filepath, target),
                                "line": line_num,
                                "vuln_id": vuln_id,
                                "severity": severity,
                                "snippet": line.strip()[:60],
                            })

        _log_activity("scan", full_name, f"{files_scanned} files, {len(findings)} findings", agent=agent_name)

        result = {
            "repo": full_name,
            "engine": "regex",
            "files_scanned": files_scanned,
            "total_findings": len(findings),
            "critical": len([f for f in findings if f["severity"] >= 0.90]),
            "high": len([f for f in findings if 0.70 <= f["severity"] < 0.90]),
            "findings": findings[:20],
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

        _post_critical_findings(name, [f for f in findings if f["severity"] >= 0.90][:3])
        return result
    finally:
        shutil.rmtree(target, ignore_errors=True)


def _post_critical_findings(repo_name: str, critical: list[dict]) -> None:
    """Best-effort: post critical findings to Vantage's signal pool."""
    if not critical:
        return
    try:
        key = open(os.path.expanduser("~/.vantage_key")).read().strip()
        for f in critical:
            payload = json.dumps({
                "symbol": repo_name[:12], "source": "strix_scan", "type": "vulnerability",
                "conviction": f["severity"], "direction": "SELL",
                "detail": f"{f['vuln_id']}: {os.path.basename(f['file'])}:{f['line']}",
            }).encode()
            req = __import__('urllib').request.Request(
                f"{VANTAGE_URL}/api/intel/signals/ingest",
                data=payload,
                headers={"Content-Type": "application/json", "X-Agent-Key": key},
            )
            __import__('urllib').request.urlopen(req, timeout=5)
    except Exception:
        pass


async def _create_scan_row(agent_id: int, owner: str, name: str, engine: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO code_scans (agent_id, owner, name, engine, status) VALUES (?, ?, ?, ?, 'pending')",
            (agent_id, owner, name, engine),
        )
        await db.commit()
        return cur.lastrowid


async def _update_scan_row(scan_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE code_scans SET {cols} WHERE id=?", (*fields.values(), scan_id))
        await db.commit()


async def _get_scan_row(scan_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM code_scans WHERE id=?", (scan_id,))).fetchone()
    return dict(row) if row else None


@router.post("/repo/{owner}/{name}/scan")
async def trigger_scan(
    owner: str, name: str,
    engine: Literal["regex", "strix"] = Query("regex"),
    agent: dict = Depends(get_agent),
):
    """Trigger a security scan on a repo.

    engine=regex (default): fast synchronous pattern scan, unchanged behavior.
    engine=strix: dispatches a real Strix (github.com/usestrix/strix) AI pentest
    run on the standalone host-side runner (STRIX_RUNNER_URL) — this can take
    minutes, so it returns immediately with a scan_id to poll via
    GET /repo/{owner}/{name}/scan/{scan_id}.
    """
    if engine == "regex":
        try:
            result = await _regex_scan(owner, name, agent["name"])
        except Exception as e:
            raise HTTPException(500, str(e))
        return result

    if not settings.STRIX_RUNNER_URL:
        raise HTTPException(503, "Strix runner not configured — set VANTAGE_STRIX_RUNNER_URL")

    full_name = f"{owner}/{name}"
    clone_url = f"{GITEA_URL}/{full_name}.git".replace("http://", f"http://{GITEA_TOKEN}@")
    scan_id = await _create_scan_row(agent["id"], owner, name, "strix")
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.post(f"{settings.STRIX_RUNNER_URL}/run", json={"clone_url": clone_url, "owner": owner, "name": name})
            r.raise_for_status()
            runner_run_id = r.json().get("run_id", "")
    except Exception as e:
        await _update_scan_row(scan_id, status="error", completed_at=datetime.now(timezone.utc).isoformat())
        raise HTTPException(502, f"Strix runner dispatch failed: {e}")

    await _update_scan_row(scan_id, runner_run_id=runner_run_id, status="running")
    _log_activity("scan", full_name, "strix run dispatched", agent=agent["name"])
    return {"scan_id": scan_id, "engine": "strix", "status": "running"}


@router.get("/repo/{owner}/{name}/scan/{scan_id}")
async def scan_status(owner: str, name: str, scan_id: int, agent: dict = Depends(get_agent)):
    """Poll an async (Strix) scan. Pulls fresh status from the runner and
    updates the local record; returns cached data once complete/errored."""
    row = await _get_scan_row(scan_id)
    if not row or row["owner"] != owner or row["name"] != name:
        raise HTTPException(404, "Scan not found")
    if row["status"] in ("complete", "error") or not settings.STRIX_RUNNER_URL or not row["runner_run_id"]:
        return {**row, "findings": json.loads(row["findings_json"])}

    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.get(f"{settings.STRIX_RUNNER_URL}/run/{row['runner_run_id']}")
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {**row, "findings": json.loads(row["findings_json"]), "poll_error": str(e)}

    status = data.get("status", row["status"])
    findings = data.get("findings", [])
    update = {"status": status, "findings_json": json.dumps(findings)}
    if status in ("complete", "error"):
        update["completed_at"] = datetime.now(timezone.utc).isoformat()
        critical = [f for f in findings if f.get("severity", 0) >= 0.90][:3]
        _post_critical_findings(name, critical)
    await _update_scan_row(scan_id, **update)
    row.update(update)
    return {**row, "findings": findings}


@router.get("/repo/{owner}/{name}/scan-results")
async def scan_results(owner: str, name: str):
    """Get the latest scan results for a repo (cached from trigger_scan)."""
    return {"repo": f"{owner}/{name}", "message": "Scan results available via POST /scan endpoint. Trigger a scan to get fresh results."}


@router.post("/repo/{owner}/{name}/memory")
async def ingest_memory(owner: str, name: str, req: MemoryIngestRequest, agent: dict = Depends(get_agent)):
    """Ingest content (e.g. a scan summary or generated-code description) into
    supermemory. Graceful no-op — returns 200 with stored=false — if
    SUPERMEMORY_URL isn't configured, matching the rest of Vantage's optional
    sidecar integrations."""
    result = await _supermemory.add_document(
        content=req.content,
        container_tag=f"vantage:{owner}/{name}",
        metadata=req.metadata,
        custom_id=req.custom_id,
    )
    if result:
        _log_activity("memory_ingest", f"{owner}/{name}", req.content[:60], agent=agent["name"])
        return {"stored": True, **result}
    return {"stored": False, "reason": "not configured or unreachable"}


@router.post("/repo/{owner}/{name}/pr")
async def create_pr(owner: str, name: str, req: CreatePRRequest, agent: dict = Depends(get_agent)):
    """Open a pull request."""
    full_name = f"{owner}/{name}"
    payload = {"title": req.title, "body": req.body, "head": req.head, "base": req.base}
    async with httpx.AsyncClient(timeout=10) as cl:
        try:
            r = await cl.post(f"{GITEA_API}/repos/{full_name}/pulls", json=payload, headers=_headers)
            if r.status_code in (200, 201):
                data = r.json()
                _log_activity("pr_opened", full_name, req.title, agent=agent["name"])
                return {"status": "created", "number": data.get("number"), "url": data.get("html_url")}
        except Exception as e:
            raise HTTPException(500, str(e))
    raise HTTPException(500, "Failed to create PR")


@router.get("/activity")
async def activity(limit: int = Query(20, ge=1, le=100)):
    """Recent activity feed — pushes, scans, PRs, repo creation."""
    return {"activity": list(reversed(_activity_feed[-limit:])), "total": len(_activity_feed)}


@router.get("/stats")
async def stats():
    """Aggregate stats across all repos."""
    overview = await code_overview()
    return {
        "total_repos": overview["total"],
        "with_strix": overview["with_hooks"],
        "total_size_kb": overview["total_size_kb"],
        "languages": overview["languages"],
        "open_prs": overview["open_prs_total"],
        "recent_commits": overview["total_commits_recent"],
        "pipeline_active": True,
        "endpoints": list(router.routes),
    }


@router.post("/search")
async def search_code(req: SearchRequest):
    """Search code across repos (grep-style)."""
    results = []
    search_dir = os.path.join(SCAN_DIR, "search_cache")
    os.makedirs(SCAN_DIR, exist_ok=True)

    repos_to_search = [req.repo] if req.repo else []
    if not repos_to_search:
        # Get all repo names
        async with httpx.AsyncClient(timeout=10) as cl:
            try:
                r = await cl.get(f"{GITEA_API}/repos/search?limit=20", headers=_headers)
                if r.status_code == 200:
                    repos_to_search = [repo["full_name"] for repo in r.json().get("data", [])]
            except: pass

    for full_name in repos_to_search[:5]:  # Limit to 5 repos
        target = os.path.join(search_dir, full_name.replace("/", "_"))
        clone_url = f"{GITEA_URL}/{full_name}.git".replace("http://", f"http://{GITEA_TOKEN}@")
        try:
            subprocess.run(["git", "clone", "--depth", "1", clone_url, target], capture_output=True, timeout=15)
            grep = subprocess.run(["grep", "-rn", "-i", req.query, target], capture_output=True, text=True)
            for line in grep.stdout.strip().split("\n")[:20]:
                if ":" in line:
                    filepath, lnum, content = line.split(":", 2)
                    results.append({
                        "repo": full_name,
                        "file": os.path.relpath(filepath, target),
                        "line": int(lnum),
                        "content": content.strip()[:120],
                    })
        except: pass
        finally:
            shutil.rmtree(target, ignore_errors=True)

    _log_activity("search", ", ".join(repos_to_search[:3]), req.query)
    return {"query": req.query, "results": results, "repos_searched": len(repos_to_search)}
