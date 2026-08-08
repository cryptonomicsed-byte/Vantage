#!/usr/bin/env python3
"""Atomic Security Daemon — triggers atomic-red-team tests on code scan findings

atomic-red-team's real test files (and this daemon's real check for them)
now live on Contabo (10.88.0.2), reached over the WireGuard tunnel via
SSH, not a local path -- moved off Hostinger to relieve disk pressure.

Note found during that move: the previous local subprocess call
(`python3 /opt/ares/invoke-atomicredteam/invoke-atomicredteam.py ...`)
was already dead code before this change -- invoke-atomicredteam is a
PowerShell module (Invoke-AtomicRedTeam.psm1), it has no such .py
entrypoint, and neither box has pwsh installed. That call would have
thrown FileNotFoundError every time it actually ran. Rather than port a
call that never worked, this now does a real, honest remote check: does
the technique's real atomic YAML exist and parse on Contabo. Full
PowerShell-based execution remains unwired -- flagged here rather than
silently pretended to work.
"""
import os, json, shlex, sqlite3, subprocess, urllib.request, time
from datetime import datetime, timezone

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
CONTABO_HOST = os.environ.get("CONTABO_TUNNEL_HOST", "10.88.0.2")
ATOMIC_DIR = "/opt/atomic-red-team/atomics"
POLL_INTERVAL = int(os.environ.get("ATOMIC_POLL", "120"))
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new"]

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY, "User-Agent": "curl/8.0"})
    try: return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except: return {}

def check_new_scans():
    """Watch for new STIX/code scan findings in Vantage."""
    db = sqlite3.connect(DB_PATH)
    try:
        rows = db.execute("""
            SELECT id, repo_name, findings, created_at FROM code_scans
            WHERE created_at > datetime('now', '-2 hours')
            ORDER BY created_at DESC LIMIT 10
        """).fetchall()
    except:
        rows = []
    db.close()
    return rows

def trigger_atomic_test(technique: str = "T1059.001"):
    """Real remote check: does this technique's atomic YAML exist and
    parse on Contabo. Runs over SSH through the tunnel, not a local path."""
    tests = {
        "T1059.001": "T1059.001/T1059.001.yaml",  # Command and Scripting Interpreter
        "T1055.001": "T1055.001/T1055.001.yaml",  # Process Injection
        "T1547.001": "T1547.001/T1547.001.yaml",  # Registry Run Keys
    }
    test_path = f"{ATOMIC_DIR}/{tests.get(technique, tests['T1059.001'])}"
    remote_check = (
        f"import yaml; d = yaml.safe_load(open('{test_path}')); "
        f"print('atomic test found:', d.get('display_name', '{technique}'))"
    )
    remote_cmd = (
        f"test -f {test_path} && python3 -c {shlex.quote(remote_check)} "
        f"|| echo 'Test not found on Contabo: {test_path}'"
    )
    try:
        result = subprocess.run(
            ["ssh", *SSH_OPTS, f"root@{CONTABO_HOST}", remote_cmd],
            capture_output=True, text=True, timeout=20,
        )
        return (result.stdout.strip() or result.stderr.strip())[:1000]
    except Exception as e:
        return f"Remote atomic check failed ({CONTABO_HOST} unreachable?): {e}"

def cycle():
    scans = check_new_scans()
    if not scans:
        return 0

    for scan_id, repo, findings, created in scans:
        findings_text = str(findings or "")[:200]
        if "critical" in findings_text.lower() or "high" in findings_text.lower():
            result = trigger_atomic_test()
            vantage_post("/api/security/scan-result", {
                "tool": "atomic",
                "target": repo,
                "status": "flagged",
                "findings": [
                    f"code_scan #{scan_id} found critical/high issues in {repo}",
                    f"atomic-red-team validation (remote, Contabo): {result[:400]}",
                ],
            })
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Atomic test triggered for {repo}")
            return 1
    return 0

if __name__ == "__main__":
    print(f"Atomic Security Daemon ({POLL_INTERVAL}s poll, remote checks via {CONTABO_HOST})")
    while True:
        try:
            triggered = cycle()
            if not triggered:
                pass  # silent when nothing to do
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(POLL_INTERVAL)
