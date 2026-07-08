#!/usr/bin/env python3
"""
OKF UNIFIED PIPELINE — Everything wired together

LAYER 1: LLM FALLBACK CHAIN
  OmniRoute (free) → LiteLLM → direct keys → llama.cpp

LAYER 2: CONTENT
  Hermes/Agent creates → Vantage feed → omokoda.duckdns.org

LAYER 3: TRADING
  9 daemons → signal pool → Vantage /api/trading/* → dashboard

LAYER 4: CODE SECURITY (Gitea → exploit → Vantage)
  Gitea push → stix_webhook → this pipeline →
    betterleaks → Strix → XSStrike → SSTImap → Nuclei → Metasploit →
    findings → Vantage feed + DB

LAYER 5: VERIFICATION (post-deploy + UI crawling)
  Playwright → Crawl4AI → clean markdown → Strix analysis → Vantage

TRIGGER: Gitea push webhook on port 9876
"""

import subprocess, json, os, sys, sqlite3
from datetime import datetime
from pathlib import Path

# ── Config ───────────────────────────────────────────────────
VANTAGE_URL = "http://localhost:8001"
VANTAGE_KEY = os.environ.get("VANTAGE_KEY","")
DB_PATH = "/opt/ares/Vantage/data/vantage.db"
SCAN_DIR = "/tmp/okf-pipeline"
STRIX_LLM = "openai/oc/deepseek-v4-flash-free"  # via OmniRoute (FREE)
LLM_API_BASE = "http://localhost:8300/v1"       # OmniRoute endpoint
LLM_API_KEY = "no-key-needed"                    # OmniRoute handles auth

TOOLS = {
    "betterleaks": {
        "cmd": ["docker", "run", "--rm", "-v", "{target}:/repo:ro",
                "ghcr.io/betterleaks/betterleaks:latest", "dir", "/repo"],
        "timeout": 120,
        "parse": lambda out: out.count("\u250c\u2500"),
    },
    "strix": {
        "cmd": ["/opt/ares/venv/bin/strix", "-n", "--target", "{target}", "--scan-mode", "quick"],
        "timeout": 300,
        "cwd": "/opt/ares/strix",
        "env": {
            "STRIX_LLM": STRIX_LLM,
            "LLM_API_KEY": LLM_API_KEY,
            "LLM_API_BASE": "http://localhost:8300/v1",
        },
    },
    "xsstrike": {
        "cmd": ["/opt/ares/venv/bin/python", "/opt/ares/XSStrike/xsstrike.py",
                "-u", "{target}", "--skip-dom", "--console-log-level", "WARNING"],
        "timeout": 120,
        "parse": lambda out: out.count("VULN") + out.count("[+] Vector"),
    },
    "sstimap": {
        "cmd": ["/opt/ares/venv/bin/python", "/opt/ares/SSTImap/sstimap.py",
                "-u", "{target}", "--no-color"],
        "timeout": 120,
        "parse": lambda out: out.count("is vulnerable") + out.count("[!]"),
    },
    "nuclei": {
        "cmd": ["nuclei", "-u", "{target}", "-silent", "-severity", "critical,high,medium", "-timeout", "10"],
        "timeout": 120,
        "parse": lambda out: len([l for l in out.split("\n") if l.strip()]),
    },
    "metasploit": {
        "cmd": ["msfconsole", "-q", "-r", "/tmp/msf_scan.rc"],
        "timeout": 120,
        "pre": lambda target: _write_msf_rc(target),
        "parse": lambda out: "VULNERABLE" in out or "[+]" in out,
    },
    "sqlmap": {
        "cmd": ["docker", "exec", "ares-parrot", "sqlmap", "-u", "{target}", "--batch", "--level=1"],
        "timeout": 120,
        "parse": lambda out: out.count("is vulnerable") + out.count("[CRITICAL]"),
    },
    "nikto": {
        "cmd": ["docker", "exec", "ares-parrot", "nikto", "-h", "{target}", "-Tuning", "123"],
        "timeout": 120,
        "parse": lambda out: len([l for l in out.split("\n") if "+" in l and "OSVDB" in l or "CVE" in l]),
    },
    "gobuster": {
        "cmd": ["docker", "exec", "ares-parrot", "gobuster", "dir", "-u", "{target}", 
                "-w", "/usr/share/wordlists/dirb/common.txt", "-q"],
        "timeout": 60,
        "parse": lambda out: len([l for l in out.split("\n") if "Status: 200" in l or "Status: 301" in l]),
    },
    "nuclei": {
        "cmd": ["nuclei", "-u", "{target}", "-silent", "-severity", "critical,high,medium", "-timeout", "10"],
        "timeout": 120,
        "parse": lambda out: len([l for l in out.split("\n") if l.strip()]),
    },
    "crawl4ai": {
        "cmd": ["/opt/ares/venv/bin/python3", "/opt/ares/crawl_vantage.py"],
        "timeout": 30,
        "parse": lambda out: len(out.split("\n")),
    },
}

def _write_msf_rc(target):
    rc = f"use auxiliary/scanner/http/xss\nset RHOSTS {target}\nset METHOD GET\nrun\nexit\n"
    with open("/tmp/msf_scan.rc", "w") as f:
        f.write(rc)

def vantage_api(method, path, data=None):
    cmd = ["curl", "-s", "-X", method, f"{VANTAGE_URL}{path}",
           "-H", "Content-Type: application/json",
           "-H", f"X-Agent-Key: {VANTAGE_KEY}"]
    if data:
        cmd += ["-d", json.dumps(data)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return json.loads(r.stdout) if r.stdout else {}

def run_tool(name, target):
    cfg = TOOLS[name]
    print(f"\n  [{name}] scanning {target}...")
    
    cmd = [arg.format(target=target) for arg in cfg["cmd"]]
    env = os.environ.copy()
    env.update(cfg.get("env", {}))
    
    if "pre" in cfg:
        cfg["pre"](target)
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=cfg["timeout"],
            cwd=cfg.get("cwd"), env=env
        )
        count = cfg["parse"](result.stdout) if "parse" in cfg else 0
        status = "✅" if count > 0 else "○"
        print(f"  [{name}] {status} {count} findings")
        return count
    except subprocess.TimeoutExpired:
        print(f"  [{name}] ⏱️ timed out")
        return -1
    except Exception as e:
        print(f"  [{name}] ❌ {e}")
        return 0

def pipeline(repo_url, repo_name=None):
    if not repo_name:
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    
    target = f"{SCAN_DIR}/{repo_name}"
    
    print(f"\n{'#'*60}")
    print(f"#  OKF UNIFIED PIPELINE: {repo_name}")
    print(f"#  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"#  betterleaks → Strix → XSStrike → SSTImap → Nuclei → Metasploit")
    print(f"{'#'*60}")
    
    subprocess.run(["rm", "-rf", target], capture_output=True)
    subprocess.run(["git", "clone", "--depth", "1", repo_url, target],
                   capture_output=True, text=True, timeout=30)
    print(f"\n  📦 Cloned: {repo_url}")
    
    # Stage 0: Crawl4AI — get clean markdown of current Vantage UI
    results = {}
    results["crawl4ai"] = run_tool("crawl4ai", "http://localhost:8001")
    
    # Security pipeline
    for name in ["betterleaks", "strix", "xsstrike", "sstimap", "sqlmap", "nuclei", "nikto", "gobuster", "metasploit"]:
        results[name] = run_tool(name, target if name in ("betterleaks", "strix") else "http://localhost:8001")
    
    # Post summary
    content = f"""**OKF Security Pipeline — {repo_name}**

| Stage | Tool | Findings |
|-------|------|----------|
| Secrets | betterleaks | {results['betterleaks']} |
| AI Analysis | Strix | {results['strix']} |
| XSS | XSStrike | {results['xsstrike']} |
| SSTI | SSTImap | {results['sstimap']} |
| CVEs/Misconfig | Nuclei | {results['nuclei']} |
| Exploit | Metasploit | {'✅' if results['metasploit'] else '○'} |
| UI Crawl | Crawl4AI | {results['crawl4ai']} lines |

**Scan time:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}
**Markdown saved:** /tmp/vantage_markdown.md (for Strix context)
"""
    
    resp = vantage_api("POST", "/api/trading/signals/ingest", {
        "title": f"🔍 Security: {repo_name}",
        "content": content,
        "tags": ["security", "pipeline", "code"],
        "status": "published",
        "content_type": "text"
    })
    print(f"\n  📤 Posted to Vantage: #{resp.get('id', '?')}")
    
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("""
            INSERT INTO broadcasts (agent_id, content_type, title, post_content, status, created_at)
            VALUES (5, 'intel', 'Security Scan', ?, 'hidden', datetime('now'))
        """, (json.dumps({"repo": repo_name, **results}),))
        db.commit(); db.close()
    except: pass
    
    print(f"\n{'='*60}")
    print(f"  ✅ Pipeline complete: {repo_name}")
    print(f"  Secrets:{results['betterleaks']} AI:{results['strix']} XSS:{results['xsstrike']} SSTI:{results['sstimap']} Nuclei:{results['nuclei']} Exploit:{results['metasploit']} Crawl:{results['crawl4ai']}")
    print(f"{'='*60}")
    return results

def handle_webhook(payload):
    repo = payload.get("repository", {})
    print(f"\n🔔 Webhook: {repo.get('full_name', '')} pushed")
    return pipeline(repo.get("clone_url", ""), repo.get("full_name", "").replace("/", "-"))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: okf_pipeline.py <repo_url> [repo_name]")
        print("  okf_pipeline.py --webhook '<json>'")
        print("  okf_pipeline.py --verify           # Playwright + Crawl4AI")
        sys.exit(1)
    
    if sys.argv[1] == "--verify":
        subprocess.run(["node", "/opt/ares/verify_vantage_ui.js"])
        run_tool("crawl4ai", "http://localhost:8001")
    elif sys.argv[1] == "--webhook":
        payload = json.loads(sys.argv[2]) if len(sys.argv) > 2 else json.load(sys.stdin)
        handle_webhook(payload)
    else:
        pipeline(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
