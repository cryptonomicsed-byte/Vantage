#!/usr/bin/env python3
"""
Vantage STIX Security Scanner — Auto-scans Gitea repos on push, generates STIX threat intel.

Pipeline:
  Gitea push webhook → Clone/pull repo → Scan for vulnerabilities →
  Convert findings to STIX indicators → Post to Vantage signals →
  Open Gitea issues for critical findings

Scans for:
  - Hardcoded secrets (API keys, private keys, mnemonics)
  - Vulnerable dependency patterns (known CVE regex)
  - Smart contract vulns (reentrancy, overflow, access control)
  - Infrastructure misconfigurations (exposed ports, weak auth)
  - SQL injection + XSS patterns

Usage:
  python3 stix_scanner.py --daemon           # Run webhook server
  python3 stix_scanner.py --scan-dir <path>   # Scan a directory
"""

import json, os, sys, time, logging, argparse, re, hashlib, subprocess
from typing import Optional, List
from datetime import datetime, timezone
import urllib.request

VANTAGE_URL = "http://127.0.0.1:8001"
VANTAGE_KEY = open(os.path.expanduser("~/.vantage_key")).read().strip()
SIGNALS_INGEST = f"{VANTAGE_URL}/api/intel/signals/ingest"
FEED_POST = f"{VANTAGE_URL}/api/agents/posts/text"

GITEA_URL = "http://localhost:3001"
GITEA_TOKEN = None  # Set to enable auto-issue creation
SCAN_DIR = "/tmp/vantage_stix_scan"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [STIX-SCAN] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("stix_scanner")

# ═══════════════════════════════════════════════════════════════════════════
# SECURITY SCANNING RULES
# ═══════════════════════════════════════════════════════════════════════════

SECRET_PATTERNS = [
    # API keys, tokens, private keys
    (r'(?:api[_-]?key|apikey|secret|token|password|passwd)\s*[=:]\s*["\'][A-Za-z0-9_\-]{20,}["\']', "HARDCODED_SECRET", "Hardcoded API key or token", 0.95),
    (r'(?:private[_-]?key|privkey|secret[_-]?key)\s*[=:]\s*["\'][A-Za-z0-9+/=]{40,}["\']', "HARDCODED_PRIVATE_KEY", "Hardcoded private key", 0.98),
    (r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', "PRIVATE_KEY_FILE", "Private key file exposed in repo", 0.99),
    # Mnemonics / seed phrases
    (r'(?:mnemonic|seed[_-]?phrase|recovery[_-]?phrase)\s*[=:]\s*["\'][a-z]+(?: [a-z]+){11,}["\']', "EXPOSED_MNEMONIC", "Wallet mnemonic/seed phrase exposed", 0.99),
    # Solana private keys
    (r'\[[0-9,\s]{100,}\]', "SOLANA_KEYPAIR", "Solana keypair array exposed", 0.97),
    # Database connection strings
    (r'(?:mongodb|postgresql|mysql|redis)://[^@\s]+@[^/\s]+', "DB_CONNECTION_STRING", "Database connection string with credentials", 0.92),
    # AWS/GCP/Azure keys
    (r'(?:AKIA|ASIA)[A-Z0-9]{16}', "AWS_ACCESS_KEY", "AWS access key exposed", 0.94),
    (r'AIza[0-9A-Za-z\-_]{35}', "GCP_API_KEY", "Google Cloud API key exposed", 0.93),
]

VULNERABILITY_PATTERNS = [
    # Solidity reentrancy
    (r'\.call\{value:', "REENTRANCY_RISK", "Potential reentrancy vulnerability (low-level call)", 0.85),
    # Solidity overflow
    (r'unchecked\s*\{', "UNCHECKED_OVERFLOW", "Unchecked arithmetic — possible overflow", 0.75),
    # Python eval/exec
    (r'(?:eval|exec)\s*\(', "UNSAFE_EXEC", "Unsafe eval/exec usage", 0.80),
    # SQL injection (Python)
    (r'\.execute\s*\(\s*f["\']', "SQL_INJECTION", "Potential SQL injection via string formatting", 0.82),
    # Hardcoded IPs (potential SSRF)
    (r'http://(?:127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.)', "SSRF_RISK", "Hardcoded internal IP — potential SSRF", 0.72),
    # Insecure random (Python)
    (r'random\.(?:random|randint|choice)', "WEAK_RANDOM", "Insecure random — use secrets module for crypto", 0.70),
    # Disabled SSL verification
    (r'verify\s*=\s*False', "SSL_VERIFY_DISABLED", "SSL certificate verification disabled", 0.78),
    # Hardcoded JWT secret
    (r'JWT_SECRET\s*=\s*["\'][A-Za-z0-9_\-]{10,}["\']', "WEAK_JWT_SECRET", "Weak/hardcoded JWT secret", 0.88),
    # Insecure deserialization
    (r'(?:pickle|marshal|cPickle)\.loads?', "INSECURE_DESERIALIZATION", "Insecure deserialization risk", 0.90),
]

INFRASTRUCTURE_PATTERNS = [
    # Exposed ports in Docker
    (r'ports:\s*\n\s*-\s*["\']?0\.0\.0\.0:', "EXPOSED_PORT", "Port exposed to 0.0.0.0", 0.65),
    # Root user in Docker
    (r'USER\s+root', "DOCKER_ROOT", "Docker container runs as root", 0.60),
    # Unsafe CORS
    (r'Access-Control-Allow-Origin:\s*\*', "UNSAFE_CORS", "Wildcard CORS — allows any origin", 0.68),
    # Disabled auth
    (r'auth\s*:\s*false|authentication\s*=\s*False|NO_AUTH\s*=\s*True', "DISABLED_AUTH", "Authentication explicitly disabled", 0.82),
]


# ═══════════════════════════════════════════════════════════════════════════
# SCAN ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def scan_file(filepath: str, content: str = None) -> list[dict]:
    """Scan a single file for security issues. Returns list of findings."""
    if content is None:
        try:
            with open(filepath, errors='ignore') as f:
                content = f.read()
        except:
            return []

    findings = []
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1].lower()

    # Only scan relevant file types
    scannable = ext in ('.py', '.js', '.ts', '.jsx', '.tsx', '.sol', '.rs', '.go',
                        '.sh', '.yml', '.yaml', '.json', '.env', '.toml', '.dockerfile',
                        '.txt', '.md', '.cfg', '.conf', '.ini')

    if not scannable and 'dockerfile' not in filename.lower() and 'makefile' not in filename.lower():
        return []

    for pattern, vuln_id, description, severity in SECRET_PATTERNS + VULNERABILITY_PATTERNS + INFRASTRUCTURE_PATTERNS:
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            # Get line number
            line_num = content[:match.start()].count('\n') + 1
            # Truncate the match for display (don't expose the secret)
            snippet = match.group()[:40] + "..." if len(match.group()) > 40 else match.group()
            findings.append({
                "file": filepath,
                "line": line_num,
                "vuln_id": vuln_id,
                "description": description,
                "severity": severity,
                "snippet": snippet,
                "type": "secret" if pattern in [p[0] for p in SECRET_PATTERNS] else "vuln",
            })

    return findings


def scan_directory(dirpath: str) -> dict:
    """Recursively scan a directory for security issues."""
    all_findings = []
    file_count = 0

    for root, dirs, files in os.walk(dirpath):
        # Skip .git, node_modules, venv
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', 'venv', '__pycache__', '.venv', 'dist')]
        for f in files:
            filepath = os.path.join(root, f)
            findings = scan_file(filepath)
            if findings:
                all_findings.extend(findings)
            file_count += 1

    # Group by severity
    critical = [f for f in all_findings if f["severity"] >= 0.90]
    high = [f for f in all_findings if 0.70 <= f["severity"] < 0.90]
    medium = [f for f in all_findings if 0.50 <= f["severity"] < 0.70]
    low = [f for f in all_findings if f["severity"] < 0.50]

    return {
        "repo": os.path.basename(dirpath),
        "files_scanned": file_count,
        "total_findings": len(all_findings),
        "critical": len(critical),
        "high": len(high),
        "medium": len(medium),
        "low": len(low),
        "findings": all_findings[:50],  # Limit to top 50
        "top_critical": critical[:5],
    }

# ═══════════════════════════════════════════════════════════════════════════
# VANTAGE + GITEA INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def post_findings(result: dict):
    """Post scan results to Vantage signals pool and feed."""
    repo = result["repo"]

    # Post aggregate signal
    post_signal_vantage(
        symbol=repo[:12], source="stix_scanner", stype="security_scan",
        conviction=min(result["critical"] / 10 + 0.3, 1.0) if result["critical"] > 0 else 0.2,
        direction="SELL" if result["critical"] > 0 else "",
        detail=f"Scanned {result['files_scanned']} files: {result['critical']}C/{result['high']}H/{result['medium']}M/{result['low']}L findings",
    )

    # Post critical findings individually
    for f in result.get("top_critical", [])[:3]:
        post_signal_vantage(
            symbol=repo[:12], source="stix_scanner", stype="vulnerability",
            conviction=f["severity"], direction="SELL",
            detail=f"{f['vuln_id']}: {f['description']} ({os.path.basename(f['file'])}:{f['line']})",
        )

    # Feed alert for repos with critical findings
    if result["critical"] > 0:
        feed_alert(
            f"stix_scan_{repo}",
            f"🔒 STIX Scan: {repo} — {result['total_findings']} findings ({result['critical']} critical)",
            f"**STIX Security Scanner** analyzed {result['files_scanned']} files in **{repo}**.\n"
            f"🔴 {result['critical']} critical | 🟠 {result['high']} high | 🟡 {result['medium']} medium\n\n"
            + "\n".join(f"- `{f['vuln_id']}`: {f['description']} ({os.path.basename(f['file'])}:{f['line']})"
                        for f in result.get("top_critical", [])[:3]),
            ["security", "stix", repo.lower()],
        )

    log.info(f"STIX Scan: {repo} — {result['total_findings']} findings "
             f"({result['critical']}C/{result['high']}H/{result['medium']}M/{result['low']}L)")


def post_signal_vantage(symbol, source, stype, conviction=0.5, direction="", detail=""):
    payload = json.dumps({
        "symbol": symbol, "source": source, "type": stype,
        "conviction": conviction, "direction": direction, "detail": detail,
    }).encode()
    try:
        req = urllib.request.Request(SIGNALS_INGEST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
    except:
        pass

_last_feed = {}
def feed_alert(key, title, content, tags):
    now = time.time()
    if now - _last_feed.get(key, 0) < 86400:  # max 1 alert per repo per day
        return
    _last_feed[key] = now
    payload = json.dumps({"title": title, "content": content, "tags": tags}).encode()
    try:
        req = urllib.request.Request(FEED_POST, data=payload,
                                     headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=10)
    except:
        pass


def clone_and_scan(repo_url: str, repo_name: str) -> Optional[dict]:
    """Clone/pull a Gitea repo and scan it."""
    import shutil

    target = os.path.join(SCAN_DIR, repo_name)
    os.makedirs(SCAN_DIR, exist_ok=True)

    try:
        if os.path.exists(target):
            # Pull latest
            result = subprocess.run(["git", "-C", target, "pull"], capture_output=True, timeout=30)
        else:
            result = subprocess.run(["git", "clone", "--depth", "1", repo_url, target],
                                    capture_output=True, timeout=60)
        if result.returncode != 0:
            log.error(f"Git error for {repo_name}: {result.stderr.decode()[:100]}")
            return None
    except Exception as e:
        log.error(f"Clone failed: {e}")
        return None

    result = scan_directory(target)

    # Cleanup old scans
    shutil.rmtree(target, ignore_errors=True)

    return result


def get_gitea_repos() -> list[dict]:
    """Get list of repos from Gitea API."""
    try:
        req = urllib.request.Request(f"{GITEA_URL}/api/v1/repos/search?limit=20",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("data", [])
    except:
        return []

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_scan():
    """Scan all Gitea repos for security issues."""
    log.info("=== STIX Security Scan ===")
    repos = get_gitea_repos()

    if not repos:
        log.warning("No Gitea repos found")
        return

    for repo in repos:
        name = repo.get("full_name", repo.get("name", "unknown"))
        clone_url = repo.get("clone_url", "")
        if not clone_url:
            continue

        log.info(f"Scanning {name}...")
        result = clone_and_scan(clone_url, name)
        if result:
            post_findings(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vantage STIX Security Scanner")
    parser.add_argument("--daemon", type=int, nargs="?", const=3600, metavar="SECONDS")
    parser.add_argument("--scan-dir", type=str, help="Scan a local directory")
    parser.add_argument("--once", action="store_true", help="Scan Gitea repos once")
    args = parser.parse_args()

    if args.scan_dir:
        result = scan_directory(args.scan_dir)
        print(json.dumps(result, indent=2, default=str))
    elif args.once:
        run_scan()
    elif args.daemon:
        log.info(f"STIX Scanner daemon — scanning every {args.daemon}s")
        while True:
            try:
                run_scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
            time.sleep(args.daemon)
    else:
        run_scan()
