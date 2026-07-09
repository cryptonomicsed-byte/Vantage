#!/usr/bin/env python3
"""Atomic Security Daemon — triggers atomic-red-team tests on code scan findings"""
import os, json, sqlite3, subprocess, urllib.request, time
from datetime import datetime, timezone

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
ATOMIC_DIR = "/opt/ares/atomic-red-team/atomics"
POLL_INTERVAL = int(os.environ.get("ATOMIC_POLL", "120"))

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY, "User-Agent": "curl/8.0"})
    try: return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except: return {}

def check_new_scans():
    """Watch for new STIX/code scan findings in Vantage."""
    db = sqlite3.connect(DB_PATH)
    # Get recent scans
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
    """Run an atomic-red-team test. T1059.001 = PowerShell execution."""
    tests = {
        "T1059.001": "T1059.001/T1059.001.yaml",  # Command and Scripting Interpreter
        "T1055.001": "T1055.001/T1055.001.yaml",  # Process Injection
        "T1547.001": "T1547.001/T1547.001.yaml",  # Registry Run Keys
    }
    test_path = f"{ATOMIC_DIR}/{tests.get(technique, tests['T1059.001'])}"
    if not os.path.exists(test_path):
        return f"Test not found: {test_path}"
    
    result = subprocess.run(
        ["python3", "/opt/ares/invoke-atomicredteam/invoke-atomicredteam.py",
         "--atomic", test_path, "--check-prereqs"],
        capture_output=True, text=True, timeout=60
    )
    return result.stdout[:1000]

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
                    f"atomic-red-team validation: {result[:400]}",
                ],
            })
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Atomic test triggered for {repo}")
            return 1
    return 0

if __name__ == "__main__":
    print(f"Atomic Security Daemon ({POLL_INTERVAL}s poll)")
    while True:
        try:
            triggered = cycle()
            if not triggered:
                pass  # silent when nothing to do
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(POLL_INTERVAL)
