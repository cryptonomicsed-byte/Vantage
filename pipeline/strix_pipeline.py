#!/usr/bin/env python3
"""
Strix Exploit Pipeline — Chains betterleaks → Strix → XSStrike → SSTImap → Metasploit → Vantage

Flow:
  Gitea push → clone repo → betterleaks (secrets) → Strix AI scan
  → XSStrike (XSS) → SSTImap (SSTI) → Metasploit (exploit) → post to Vantage
"""
import subprocess, json, os, sys, time, sqlite3
from datetime import datetime
from pathlib import Path

VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
SCAN_DIR = "/tmp/strix-pipeline"

# ── API helpers ──────────────────────────────────────────────
def vantage_post(endpoint, data):
    cmd = ["curl", "-s", "-X", "POST", f"{VANTAGE_URL}{endpoint}",
           "-H", "Content-Type: application/json",
           "-H", f"X-Agent-Key: {VANTAGE_KEY}",
           "-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return json.loads(r.stdout) if r.stdout else {}

# ── Step 0: betterleaks secrets scan ──────────────────────────
def run_betterleaks(target_path):
    print(f"\n  betterleaks: scanning {target_path}")
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{target_path}:/repo:ro",
             "ghcr.io/betterleaks/betterleaks:latest", "dir", "/repo"],
            capture_output=True, text=True, timeout=120
        )
        leaks = result.stdout.count("┌─")  # each leak starts with this char
        print(f"  betterleaks: {leaks} secrets found")
        return leaks, result.stdout
    except Exception as e:
        print(f"  ⚠️ betterleaks error: {e}")
        return 0, str(e)

# ── Step 1: Strix AI Scan ────────────────────────────────────
def run_strix(target_path, mode="quick"):
    print(f"\n{'='*60}")
    print(f"  STRIX AI SCAN: {target_path}")
    print(f"{'='*60}")
    
    env = os.environ.copy()
    env["STRIX_LLM"] = "deepseek/deepseek-chat"
    env["LLM_API_KEY"] = "sk-bd3dade513374b9f88cefbccc80b629b"
    
    try:
        result = subprocess.run(
            ["/opt/ares/venv/bin/strix", "-n", "--target", target_path,
             "--scan-mode", mode],
            capture_output=True, text=True, timeout=300,
            env=env, cwd="/opt/ares/strix"
        )
        output = result.stdout + result.stderr
        print(f"  Exit: {result.returncode}")
        
        # Parse findings from strix_runs/
        runs_dir = Path("/opt/ares/strix/strix_runs")
        findings = []
        if runs_dir.exists():
            for run in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                report = run / "report.json"
                if report.exists():
                    with open(report) as f:
                        data = json.load(f)
                    findings = data.get("findings", [])
                    break
        
        print(f"  Findings: {len(findings)}")
        return findings, output
    except subprocess.TimeoutExpired:
        print("  ⚠️ Strix timed out after 300s")
        return [], "TIMEOUT"
    except Exception as e:
        print(f"  ❌ Strix error: {e}")
        return [], str(e)

# ── Step 2: XSStrike (for XSS findings) ─────────────────────
def run_xsstrike(url):
    print(f"\n  XSStrike: scanning {url}")
    try:
        result = subprocess.run(
            ["/opt/ares/venv/bin/python", "/opt/ares/XSStrike/xsstrike.py",
             "-u", url, "--skip-dom", "--console-log-level", "WARNING"],
            capture_output=True, text=True, timeout=120
        )
        # Parse XSStrike output for vulnerability counts
        output = result.stdout
        vulns = output.count("VULN") + output.count("[+] Vector") + output.count("Reflected")
        print(f"  XSStrike findings: {vulns} potential XSS")
        return vulns, output
    except Exception as e:
        print(f"  ⚠️ XSStrike error: {e}")
        return 0, str(e)

# ── Step 3: SSTImap (for SSTI findings) ──────────────────────
def run_sstimap(url):
    print(f"\n  SSTImap: scanning {url}")
    try:
        result = subprocess.run(
            ["/opt/ares/venv/bin/python", "/opt/ares/SSTImap/sstimap.py",
             "-u", url, "--no-color"],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout
        vulns = output.count("is vulnerable") + output.count("[!]")
        print(f"  SSTImap findings: {vulns} potential SSTI")
        return vulns, output
    except Exception as e:
        print(f"  ⚠️ SSTImap error: {e}")
        return 0, str(e)

# ── Step 4: Metasploit exploit validation ────────────────────
def run_metasploit(target_url, vuln_type="xss"):
    print(f"\n  Metasploit: testing exploit for {vuln_type}")
    
    msf_rc = f"""
use auxiliary/scanner/http/xss
set RHOSTS {target_url}
set METHOD GET
run
exit
"""
    rc_file = "/tmp/msf_scan.rc"
    with open(rc_file, "w") as f:
        f.write(msf_rc)
    
    try:
        result = subprocess.run(
            ["msfconsole", "-q", "-r", rc_file],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout
        # Check for successful exploitation markers
        success = "VULNERABLE" in output or "[+]" in output
        print(f"  Metasploit: {'✅ EXPLOITABLE' if success else 'no exploit'}")
        return success, output
    except Exception as e:
        print(f"  ⚠️ Metasploit error: {e}")
        return False, str(e)

# ── Step 5: Post to Vantage ──────────────────────────────────
def post_to_vantage(repo_name, betterleaks_count, strix_findings, xsstrike_count, sstimap_count, msf_exploited):
    title = f"🔍 Security Scan: {repo_name}"
    
    finding_lines = []
    for f in strix_findings[:5]:
        finding_lines.append(
            f"- **{f.get('title', 'Finding')}** ({f.get('severity', '?')})\n"
            f"  {f.get('description', '')[:200]}"
        )
    
    content = f"""**Strix Pipeline Scan — {repo_name}**

**Secrets (betterleaks):** {betterleaks_count} exposed
**AI Analysis (Strix):** {len(strix_findings)} findings
{chr(10).join(finding_lines) if finding_lines else '  No findings'}

**XSS Scanner (XSStrike):** {xsstrike_count} potential XSS vectors
**SSTI Scanner (SSTImap):** {sstimap_count} potential SSTI vectors

**Exploit Validation (Metasploit):** {'✅ Exploitable' if msf_exploited else 'No exploit confirmed'}

**Scan time:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}
"""
    
    resp = vantage_post("/api/agents/posts/text", {
        "title": title,
        "content": content,
        "tags": ["security", "strix", "pentest", "code"],
        "status": "published",
        "content_type": "text"
    })
    print(f"  Posted to Vantage: {resp.get('id', '?')}")

# ── Main Pipeline ────────────────────────────────────────────
def pipeline(repo_url, repo_name):
    print(f"\n{'#'*60}")
    print(f"#  STRIX PIPELINE: {repo_name}")
    print(f"#  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")
    
    # Clone repo
    target = f"{SCAN_DIR}/{repo_name}"
    subprocess.run(["rm", "-rf", target], capture_output=True)
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, target],
        capture_output=True, text=True, timeout=30
    )
    print(f"  Clone: {repo_url} → {target}")
    
    # Stage 0: betterleaks secrets scan
    betterleaks_count, _ = run_betterleaks(target)
    
    # Stage 1: Strix AI
    findings, strix_output = run_strix(target)
    
    # Stage 2: XSStrike (if XSS-related findings)
    xsstrike_count = 0
    xss_findings = [f for f in findings if "xss" in str(f.get("title", "")).lower()]
    if xss_findings:
        xsstrike_count, _ = run_xsstrike("http://localhost:8001")
    
    # Stage 3: SSTImap (if SSTI-related findings)
    sstimap_count = 0
    ssti_findings = [f for f in findings if "ssti" in str(f.get("title", "")).lower() or "template" in str(f.get("title", "")).lower()]
    if ssti_findings:
        sstimap_count, _ = run_sstimap("http://localhost:8001")
    
    # Stage 4: Metasploit
    msf_exploited = False
    if findings:
        msf_exploited, _ = run_metasploit("http://localhost:8001")
    
    # Stage 5: Post to Vantage
    post_to_vantage(repo_name, betterleaks_count, findings, xsstrike_count, sstimap_count, msf_exploited)
    
    # Stage 6: Store findings in DB
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("""
            INSERT INTO broadcasts (agent_id, content_type, title, post_content, status, created_at)
            VALUES (5, 'intel', 'Security Scan Complete', ?, 'hidden', datetime('now'))
        """, (json.dumps({"repo": repo_name, "findings": len(findings), "betterleaks": betterleaks_count, "xsstrike": xsstrike_count, "sstimap": sstimap_count, "msf_exploited": msf_exploited}),))
        db.commit()
        db.close()
    except: pass
    
    print(f"\n  ✅ Pipeline complete: {repo_name}")
    return findings

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: strix_pipeline.py <repo_url> [repo_name]")
        sys.exit(1)
    
    repo_url = sys.argv[1]
    repo_name = sys.argv[2] if len(sys.argv) > 2 else repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    
    findings = pipeline(repo_url, repo_name)
    print(f"\nDone: {len(findings)} findings")
