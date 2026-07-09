#!/usr/bin/env python3
"""Bridge: SSTImap + XSStrike → Vantage security pipeline"""
import os, json, subprocess, urllib.request, sys
from datetime import datetime, timezone

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
SSTIMAP = "/opt/ares/SSTImap/sstimap.py"
XSSTRIKE = "/opt/ares/XSStrike/xsstrike.py"

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY, "User-Agent": "curl/8.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def scan_ssti(target: str) -> dict:
    """Run SSTImap against target."""
    result = subprocess.run(
        ["python3", SSTIMAP, "-u", target, "--no-interactive"],
        capture_output=True, text=True, timeout=120
    )
    findings = []
    for line in result.stdout.split("\n"):
        if "vulnerable" in line.lower() or "inject" in line.lower():
            findings.append(line.strip()[:200])
    return {
        "tool": "SSTImap",
        "target": target,
        "vulnerable": len(findings) > 0,
        "findings": findings[:10],
        "raw_len": len(result.stdout)
    }

def scan_xss(target: str) -> dict:
    """Run XSStrike against target."""
    result = subprocess.run(
        ["python3", XSSTRIKE, "-u", target, "--crawl", "--blind"],
        capture_output=True, text=True, timeout=180
    )
    findings = []
    for line in result.stdout.split("\n"):
        if "reflected" in line.lower() or "dom" in line.lower() or "vulnerable" in line.lower():
            findings.append(line.strip()[:200])
    return {
        "tool": "XSStrike",
        "target": target,
        "vulnerable": len(findings) > 0,
        "findings": findings[:10],
        "raw_len": len(result.stdout)
    }

def ingest_scan_result(result: dict):
    """Post scan findings to Vantage's security-scans pipeline as a structured
    record — surfaces in the ARES SENTINEL Security Scans tab, not just the feed."""
    return vantage_post("/api/security/scan-result", {
        "tool": result["tool"].lower(),
        "target": result["target"],
        "vulnerable": bool(result["vulnerable"]),
        "findings": result["findings"][:20],
    })

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        print("Usage: python3 security_bridge.py <target_url>")
        print("Example: python3 security_bridge.py https://example.com/page?param=test")
        sys.exit(1)

    print(f"[security-bridge] Scanning {target}...")
    for scan_func in [scan_ssti, scan_xss]:
        try:
            result = scan_func(target)
            ingest_scan_result(result)
            print(f"  {result['tool']}: {'VULNERABLE' if result['vulnerable'] else 'CLEAN'} ({len(result['findings'])} findings)")
        except Exception as e:
            print(f"  {scan_func.__name__}: ERROR - {e}")
