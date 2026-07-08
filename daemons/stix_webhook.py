#!/usr/bin/env python3
"""
STIX Webhook Server — Receives Gitea push events, scans changed files,
posts findings as PR comments. Part of the OpenCode → Gitea pipeline.

Flow:
  OpenCode generates code → pushes to Gitea
  Gitea fires push webhook → this server receives it
  Scans changed files for secrets/vulns
  Posts findings as PR comments on Gitea
  Posts findings to Vantage signals pool

Usage:
  python3 stix_webhook.py --port 9876
"""

import json, os, sys, time, logging, argparse, re, hashlib, hmac, threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
import subprocess
import urllib.request

# ── Config ──────────────────────────────────────────────────────────────

VANTAGE_URL = "http://127.0.0.1:8001"
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
GITEA_URL = "http://localhost:3001"
GITEA_API = f"{GITEA_URL}/api/v1"
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("STIX_WEBHOOK_SECRET", "vantage-stix-webhook-2026")
SCAN_DIR = "/tmp/vantage_stix_scan"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [STIX-WEBHOOK] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("stix_webhook")

# ── Security patterns (same as stix_scanner) ────────────────────────────

SECRET_PATTERNS = [
    (r'(?:api[_-]?key|apikey|secret|token|password|passwd)\s*[=:]\s*["\'][A-Za-z0-9_\-]{20,}["\']', "HARDCODED_SECRET", "Hardcoded API key or token", 0.95),
    (r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', "PRIVATE_KEY", "Private key exposed in repo", 0.99),
    (r'(?:mnemonic|seed[_-]?phrase)\s*[=:]\s*["\'][a-z]+(?: [a-z]+){11,}["\']', "EXPOSED_MNEMONIC", "Wallet mnemonic exposed", 0.99),
    (r'(?:mongodb|postgresql|mysql|redis)://[^@\s]+@[^/\s]+', "DB_CREDENTIALS", "Database credentials in code", 0.92),
    (r'(?:AKIA|ASIA)[A-Z0-9]{16}', "AWS_KEY", "AWS access key exposed", 0.94),
]

VULN_PATTERNS = [
    (r'\.call\{value:', "REENTRANCY", "Potential reentrancy — low-level call", 0.85),
    (r'(?:eval|exec)\s*\(', "UNSAFE_EXEC", "Unsafe eval/exec usage", 0.80),
    (r'\.execute\s*\(\s*f["\']', "SQL_INJECTION", "Potential SQL injection", 0.82),
    (r'verify\s*=\s*False', "SSL_VERIFY_OFF", "SSL verification disabled", 0.78),
    (r'(?:pickle|marshal)\.loads?', "INSECURE_DESERIALIZE", "Insecure deserialization", 0.90),
    (r'random\.(?:random|randint|choice)', "WEAK_RANDOM", "Use secrets module for crypto", 0.70),
    (r'JWT_SECRET\s*=\s*["\'][A-Za-z0-9_\-]{10,}["\']', "WEAK_JWT", "Weak JWT secret", 0.88),
]

ALL_PATTERNS = [(p[0], p[1], p[2], p[3], "secret") for p in SECRET_PATTERNS] + \
               [(p[0], p[1], p[2], p[3], "vuln") for p in VULN_PATTERNS]


def scan_diff_content(content: str, filename: str) -> list[dict]:
    """Scan changed file content for security issues."""
    findings = []
    for line_num, line in enumerate(content.split('\n'), 1):
        if not line.startswith('+') or line.startswith('+++'):
            continue  # Only scan added lines
        code = line[1:]  # Strip the '+' prefix
        for pattern, vuln_id, desc, severity, category in ALL_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                snippet = code.strip()[:60]
                findings.append({
                    "file": filename,
                    "line": line_num,
                    "vuln_id": vuln_id,
                    "description": desc,
                    "severity": severity,
                    "category": category,
                    "snippet": snippet,
                })
    return findings


def post_vantage_signal(symbol, source, stype, conviction, detail=""):
    """Post to Vantage signals pool."""
    payload = json.dumps({
        "symbol": symbol, "source": source, "type": stype,
        "conviction": conviction, "direction": "SELL", "detail": detail,
    }).encode()
    try:
        req = urllib.request.Request(
            f"{VANTAGE_URL}/api/intel/signals/ingest",
            data=payload, headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass


def post_gitea_comment(owner: str, repo: str, issue_index: int, body: str):
    """Post a comment on a Gitea issue/PR."""
    if not GITEA_TOKEN:
        log.info(f"Would comment on {owner}/{repo}#{issue_index}: {body[:80]}")
        return

    try:
        url = f"{GITEA_API}/repos/{owner}/{repo}/issues/{issue_index}/comments"
        payload = json.dumps({"body": body}).encode()
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": f"token {GITEA_TOKEN}"})
        urllib.request.urlopen(req, timeout=10)
        log.info(f"Comment posted on {owner}/{repo}#{issue_index}")
    except Exception as e:
        log.error(f"Comment failed: {e}")


def build_comment_body(repo_full: str, branch: str, commit: str, findings: list[dict]) -> str:
    """Build a Gitea comment with scan results."""
    critical = [f for f in findings if f["severity"] >= 0.90]
    high = [f for f in findings if 0.70 <= f["severity"] < 0.90]

    lines = [
        f"## 🔒 STIX Security Scan",
        f"",
        f"**Branch:** `{branch}` | **Commit:** `{commit[:8]}`",
        f"",
        f"| Severity | Count |",
        f"|----------|-------|",
        f"| 🔴 Critical | {len(critical)} |",
        f"| 🟠 High | {len(high)} |",
        f"| **Total** | **{len(findings)}** |",
        f"",
    ]

    if critical:
        lines.append("### 🚨 Critical Findings")
        for f in critical[:5]:
            lines.append(f"- **`{f['vuln_id']}`**: {f['description']}")
            lines.append(f"  - `{f['file']}:{f['line']}` → `{f['snippet']}`")

    if high:
        lines.append("### ⚠️ High Severity")
        for f in high[:5]:
            lines.append(f"- **`{f['vuln_id']}`**: {f['description']} — `{f['file']}:{f['line']}`")

    if not findings:
        lines.append("✅ No security issues found in this push.")

    lines.append(f"\n---\n*Scanned by Vantage STIX Security Scanner*")

    return "\n".join(lines)


class WebhookHandler(BaseHTTPRequestHandler):
    """Handle incoming Gitea webhook POSTs."""

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        # Verify signature
        sig = self.headers.get('X-Gitea-Signature', '')
        if WEBHOOK_SECRET:
            expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
            if sig != expected and sig != f"sha256={expected}":
                self.send_response(403)
                self.end_headers()
                return

        try:
            event = json.loads(body)
        except:
            self.send_response(400)
            self.end_headers()
            return

        event_type = self.headers.get('X-Gitea-Event', 'push')
        log.info(f"Webhook received: {event_type}")

        # Handle push events
        if event_type == 'push':
            self.handle_push(event)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def handle_push(self, event: dict):
        """Process a push event: scan changed files, post results."""
        repo = event.get("repository", {})
        repo_full = repo.get("full_name", "unknown")
        branch = (event.get("ref", "")).replace("refs/heads/", "")
        commits = event.get("commits", [])
        head_commit = event.get("head_commit", {})
        commit_id = head_commit.get("id", commits[0].get("id", "") if commits else "")

        # Collect changed files from all commits
        changed_files = set()
        for commit in commits:
            changed_files.update(commit.get("added", []))
            changed_files.update(commit.get("modified", []))

        log.info(f"Push to {repo_full} ({branch}): {len(changed_files)} files changed in {len(commits)} commits")

        # Clone repo and scan changed files
        clone_url = repo.get("clone_url", "")
        if not clone_url:
            return

        target = os.path.join(SCAN_DIR, repo.get("name", "repo"))
        os.makedirs(SCAN_DIR, exist_ok=True)

        try:
            if os.path.exists(target):
                subprocess.run(["git", "-C", target, "fetch", "--depth", "1", "origin", branch],
                              capture_output=True, timeout=30)
                subprocess.run(["git", "-C", target, "checkout", commit_id],
                              capture_output=True, timeout=10)
            else:
                subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, clone_url, target],
                              capture_output=True, timeout=60)
        except Exception as e:
            log.error(f"Git error: {e}")
            return

        # Run OKF unified security pipeline (betterleaks→Strix→XSStrike→SSTImap→Metasploit)
        try:
            log.info(f"Launching OKF pipeline for {repo_full}")
            subprocess.Popen(
                ["/opt/ares/venv/bin/python3", "/opt/ares/okf_pipeline.py", clone_url, repo_full.replace("/", "-")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception as e:
            log.error(f"OKF pipeline launch failed: {e}")

        # Scan only changed files
        all_findings = []
        for fpath in changed_files:
            full_path = os.path.join(target, fpath)
            if not os.path.exists(full_path):
                continue
            try:
                with open(full_path, errors='ignore') as f:
                    content = f.read()
            except:
                continue
            findings = scan_diff_content(content, fpath)
            all_findings.extend(findings)

        # Cleanup
        import shutil
        shutil.rmtree(target, ignore_errors=True)

        if not all_findings:
            log.info(f"Scan complete: 0 findings for {repo_full}")
            return

        log.info(f"Scan complete: {len(all_findings)} findings")

        # Post to Vantage
        for f in all_findings[:10]:
            post_vantage_signal(
                symbol=repo_full.split("/")[-1][:12],
                source="stix_webhook",
                stype="code_vuln",
                conviction=f["severity"],
                detail=f"{f['vuln_id']}: {f['description']} ({f['file']}:{f['line']})",
            )

        # Find associated PR and comment
        # Gitea push events don't directly link to PRs — search for open PRs on this branch
        try:
            owner, repo_name = repo_full.split("/")
            prs_url = f"{GITEA_API}/repos/{owner}/{repo_name}/pulls?state=open&head={branch}"
            req = urllib.request.Request(prs_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                prs = json.loads(r.read())
                if prs:
                    pr_number = prs[0].get("number")
                    comment = build_comment_body(repo_full, branch, commit_id, all_findings)
                    post_gitea_comment(owner, repo_name, pr_number, comment)
        except:
            pass

    def log_message(self, format, *args):
        log.debug(f"HTTP: {format % args}")


def run_server(port: int = 9876):
    """Start the webhook server."""
    server = HTTPServer(('0.0.0.0', port), WebhookHandler)
    log.info(f"STIX Webhook Server listening on port {port}")
    log.info(f"Configure Gitea webhook: {GITEA_URL}/<owner>/<repo>/settings/hooks")
    log.info(f"  Payload URL: http://<vps-ip>:{port}/")
    log.info(f"  Content type: application/json")
    log.info(f"  Secret: {WEBHOOK_SECRET}")
    log.info(f"  Events: Push")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STIX Webhook Server for Gitea")
    parser.add_argument("--port", type=int, default=9876, help="Listen port")
    args = parser.parse_args()
    run_server(args.port)
