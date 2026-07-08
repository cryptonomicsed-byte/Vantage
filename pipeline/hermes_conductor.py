#!/usr/bin/env python3
"""
Hermes Pipeline Conductor — Autonomous Site Intelligence Pipeline.

Orchestrates: seo-os + Strix + OpenCode → supermemory → Gitea → Vantage
Trigger: "audit example.com"

Flow:
  1. Parse task → dispatch seo-os (SEO) + Strix (pentest) + OpenCode (fixer) in parallel
  2. Collect reports → push to Gitea
  3. Update supermemory with client context
  4. Post summary to Vantage
  5. Monitor via herdr
"""

import subprocess, json, os, sys, time, threading
from datetime import datetime
from pathlib import Path

GITEA_URL = "http://localhost:3001"
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "")
WORKSPACE = "/opt/ares/agent-workspace"
SUPERMEMORY_URL = "http://localhost:3002"
VANTAGE_KEY = open("/opt/ares/.vantage_key").read().strip()
VANTAGE_URL = "http://localhost:8001"

def run(cmd, cwd=None, timeout=120):
    """Run a shell command and return output."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return r.stdout + r.stderr
    except Exception as e:
        return str(e)

def audit(target: str):
    """Full autonomous audit pipeline."""
    print(f"\n{'='*60}")
    print(f"HERMES: Auditing {target}")
    print(f"{'='*60}")
    start = time.time()
    results = {"target": target, "timestamp": datetime.now().isoformat(), "seo": None, "pentest": None, "fixes": []}

    # Phase 1: Parallel sweep — SEO + Pentest
    print("\n[HERMES] Phase 1: Parallel sweep...")
    threads = {}

    def run_seo():
        print(f"  [seo-os] Scanning {target}...")
        # SEO audit via DeepSeek
        prompt = f"Perform a full SEO audit of {target}. Check: Core Web Vitals, schema markup, meta tags, headings, backlinks profile, content quality, mobile responsiveness, AI search readiness, GEO optimization. Output a structured JSON report."
        out = subprocess.run(["opencode", "run", prompt], capture_output=True, text=True, cwd=WORKSPACE, timeout=120).stdout
        # Save SEO report
        report = {"source": "seo-os", "target": target, "findings": out[:2000]}
        report_path = Path(WORKSPACE) / "reports" / f"seo-{target.replace('.','-')}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        results["seo"] = str(report_path)
        print(f"  [seo-os] Done → {report_path}")

    def run_pentest():
        print(f"  [Strix] Pentesting {target}...")
        # Security pentest via Strix
        os.environ["PATH"] = "/opt/ares/venv/bin:" + os.environ.get("PATH", "")
        out = run(f"/opt/ares/venv/bin/strix scan {target} 2>/dev/null || /opt/ares/venv/bin/python3 -m strix scan {target} 2>/dev/null || echo 'strix not ready'")
        report = {"source": "strix", "target": target, "findings": out[:2000]}
        report_path = Path(WORKSPACE) / "reports" / f"pentest-{target.replace('.','-')}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        results["pentest"] = str(report_path)
        print(f"  [Strix] Done → {report_path}")

    threads["seo"] = threading.Thread(target=run_seo)
    threads["pentest"] = threading.Thread(target=run_pentest)
    for t in threads.values(): t.start()
    for t in threads.values(): t.join()

    # Phase 2: Fix generation via OpenCode
    print("\n[HERMES] Phase 2: Generating fixes...")
    if results["seo"] or results["pentest"]:
        prompt = f"Review the SEO and pentest reports for {target}. Generate fix branches with remediation code. Create a summary PR."
        out = subprocess.run(["opencode", "run", prompt], capture_output=True, text=True, cwd=WORKSPACE, timeout=120).stdout
        results["fixes"].append(out[:500])

    # Phase 3: Push to Gitea
    print("\n[HERMES] Phase 3: Pushing to Gitea...")
    out = run("git add -A && git commit -m 'hermes: audit {target}' && git push origin main", cwd=WORKSPACE)
    print(f"  Git: {out[:100]}")

    # Phase 4: Update supermemory
    print("\n[HERMES] Phase 4: Updating supermemory...")
    try:
        import urllib.request
        payload = json.dumps({"client": target, "type": "audit", "findings": results}).encode()
        req = urllib.request.Request(f"{SUPERMEMORY_URL}/api/ingest", data=payload,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        print("  supermemory updated")
    except:
        print("  supermemory unavailable")

    # Phase 5: Post to Vantage
    print("\n[HERMES] Phase 5: Posting to Vantage...")
    try:
        import urllib.request
        payload = json.dumps({
            "title": f"Audit: {target}",
            "content": f"Autonomous audit complete.\nSEO report: {results['seo']}\nPentest: {results['pentest']}",
            "tags": json.dumps(["audit", target, "hermes-pipeline"])
        }).encode()
        req = urllib.request.Request(f"{VANTAGE_URL}/api/agents/posts/text", data=payload,
            headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY})
        urllib.request.urlopen(req, timeout=5)
        print("  Posted to Vantage")
    except Exception as e:
        print(f"  Vantage: {e}")

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"HERMES: Audit complete in {elapsed:.1f}s")
    print(f"  SEO: {results['seo']}")
    print(f"  Pentest: {results['pentest']}")
    print(f"  Fixes: {len(results['fixes'])} generated")
    print(f"{'='*60}")

def status():
    """Check pipeline health."""
    import urllib.request
    print("=== HERMES PIPELINE STATUS ===")
    s = {
        "OpenCode": run("opencode --version", timeout=5).strip(),
        "Gitea": {"version": json.loads(run(f"curl -s {GITEA_URL}/api/v1/version")).get("version","?")} if "{" in run(f"curl -s {GITEA_URL}/api/v1/version") else "down",
        "supermemory": "running" if "307" in run(f"curl -s -o /dev/null -w '%{{http_code}}' {SUPERMEMORY_URL}") else "down",
        "STIX webhook": "running" if "stix_webhook" in run("pgrep -a stix_webhook || true") else "down",
        "STIX scanner": "running" if "stix_scanner" in run("pgrep -a stix_scanner || true") else "down",
        "herdr": "built" if Path("/opt/ares/herdr/target/release/herdr").exists() else "not built",
        "Vantage": "running" if "200" in run(f"curl -s -o /dev/null -w '%{{http_code}}' {VANTAGE_URL}") else "down",
        "supermemory": "running" if "307" in run(f"curl -s -o /dev/null -w '%{{http_code}}' {SUPERMEMORY_URL}") else "down",
    }
    for k, v in s.items():
        val = str(v).replace("{","").replace("}","")[:60]
        print(f"  {k}: {val}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: hermes-conductor audit <target> | status")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "audit":
        audit(sys.argv[2] if len(sys.argv) > 2 else "example.com")
    elif cmd == "status":
        status()
    else:
        print(f"Unknown: {cmd}")
