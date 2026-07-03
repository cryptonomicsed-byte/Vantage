"""Code Collaboration API v2 — Agent-first endpoints for the full pipeline.

Every action an agent needs:
  GET  /api/code/overview          — All repos with status
  GET  /api/code/repo/{owner}/{name} — Single repo detail
  POST /api/code/repo/create        — Create a new repo
  POST /api/code/repo/{owner}/{name}/push — Push file content
  POST /api/code/repo/{owner}/{name}/scan — Trigger STIX scan
  GET  /api/code/repo/{owner}/{name}/scan-results — Latest scan
  GET  /api/code/activity           — Recent activity feed
  GET  /api/code/stats              — Aggregate stats
  POST /api/code/repo/{owner}/{name}/pr — Open a PR
  POST /api/code/search             — Search code across repos
"""

import logging, httpx, json, os, time, base64, subprocess, shutil, re
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Query, Body, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/code", tags=["code"])

GITEA_URL = "http://2.25.70.156:3001"
GITEA_API = f"{GITEA_URL}/api/v1"
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "2551cd513d981914a5be801068e797eb7e1878ac")
SCAN_DIR = "/tmp/vantage_code_scan"
VANTAGE_URL = "http://127.0.0.1:8001"

_headers = {"Accept": "application/json", "Authorization": f"token {GITEA_TOKEN}"}
_cache = {"data": None, "ts": 0}
_activity_feed: list[dict] = []
_activity_max = 100

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
    """Get all repos with STIX status, commits, PRs, branches."""
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
                "stix_scan_status": "unknown",
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

            # STIX webhooks
            try:
                hooks = await cl.get(f"{GITEA_API}/repos/{full_name}/hooks", headers=_headers)
                if hooks.status_code == 200:
                    for h in hooks.json():
                        config = h.get("config", {})
                        if "9876" in config.get("url", ""):
                            info["webhooks"].append({
                                "id": h.get("id"),
                                "type": "stix_scan",
                                "active": h.get("active", False),
                                "url": config.get("url", ""),
                            })
            except: pass

            info["stix_scan_status"] = "active" if info["webhooks"] else "no_hook"
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
    """Comprehensive repo profile — STIX, Gitea stats, collaborators, commits."""
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
        "stix_webhooks": [], "stix_active": False,
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

        # STIX webhooks
        try:
            hooks = await cl.get(f"{GITEA_API}/repos/{full_name}/hooks", headers=_headers)
            if hooks.status_code == 200:
                for h in hooks.json():
                    config = h.get("config", {})
                    result["stix_webhooks"].append({
                        "id": h.get("id"), "type": h.get("type"),
                        "active": h.get("active", False),
                        "url": config.get("url", ""),
                        "events": h.get("events", []),
                    })
            result["stix_active"] = len(result["stix_webhooks"]) > 0
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
                # Auto-register STIX webhook
                webhook_payload = {
                    "type": "gitea",
                    "config": {"url": "http://localhost:9876/", "content_type": "json", "secret": "vantage-stix-webhook-2026"},
                    "events": ["push"], "active": True,
                }
                await cl.post(f"{GITEA_API}/repos/ares-bot/{req.name}/hooks", json=webhook_payload, headers=_headers)
                return {"status": "created", "repo": data.get("full_name", req.name),
                        "html_url": f"{GITEA_URL}/ares-bot/{req.name}",
                        "stix_hook": "auto-registered"}
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


@router.post("/repo/{owner}/{name}/scan")
async def trigger_scan(owner: str, name: str, agent: dict = Depends(get_agent)):
    """Trigger a STIX security scan on a repo."""
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

        _log_activity("scan", full_name, f"{files_scanned} files, {len(findings)} findings", agent=agent["name"])

        result = {
            "repo": full_name,
            "files_scanned": files_scanned,
            "total_findings": len(findings),
            "critical": len([f for f in findings if f["severity"] >= 0.90]),
            "high": len([f for f in findings if 0.70 <= f["severity"] < 0.90]),
            "findings": findings[:20],
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

        # Post critical findings to Vantage signals
        if result["critical"] > 0:
            try:
                for f in [x for x in findings if x["severity"] >= 0.90][:3]:
                    key = open(os.path.expanduser("~/.vantage_key")).read().strip()
                    payload = json.dumps({
                        "symbol": name[:12], "source": "stix_scan", "type": "vulnerability",
                        "conviction": f["severity"], "direction": "SELL",
                        "detail": f"{f['vuln_id']}: {os.path.basename(f['file'])}:{f['line']}",
                    }).encode()
                    req = __import__('urllib').request.Request(
                        f"{VANTAGE_URL}/api/intel/signals/ingest",
                        data=payload,
                        headers={"Content-Type": "application/json", "X-Agent-Key": key},
                    )
                    __import__('urllib').request.urlopen(req, timeout=5)
            except: pass

        return result
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        shutil.rmtree(target, ignore_errors=True)


@router.get("/repo/{owner}/{name}/scan-results")
async def scan_results(owner: str, name: str):
    """Get the latest scan results for a repo (cached from trigger_scan)."""
    return {"repo": f"{owner}/{name}", "message": "Scan results available via POST /scan endpoint. Trigger a scan to get fresh results."}


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
        "with_stix": overview["with_hooks"],
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
