#!/usr/bin/env python3
"""Bridge: GEF (GDB Enhanced Features) → Vantage security pipeline
Runs automated binary analysis with GEF and posts findings."""
import os, json, subprocess, urllib.request, sys, tempfile
from datetime import datetime, timezone

VANTAGE_URL = os.environ.get("VANTAGE_URL", "http://localhost:8001")
VANTAGE_KEY = os.environ.get("VANTAGE_KEY", "")
GEF_PATH = "/opt/ares/gef/gef.py"

def vantage_post(endpoint, data):
    req = urllib.request.Request(f"{VANTAGE_URL}{endpoint}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Agent-Key": VANTAGE_KEY, "User-Agent": "curl/8.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def analyze_binary(binary_path: str, input_data: str = "") -> dict:
    """Run GDB+GEF analysis on a binary."""
    if not os.path.exists(binary_path):
        return {"error": f"Binary not found: {binary_path}"}
    if not os.path.exists(GEF_PATH):
        return {"error": f"GEF not found: {GEF_PATH}"}

    # Build GDB script
    gdb_commands = [
        f"source {GEF_PATH}",
        "set pagination off",
        "set confirm off",
        f"file {binary_path}",
        "checksec",
        "elf-info",
        "entry-break",
        "run",
        "vmmap",
        "heap chunks",
        "registers",
        "dereference $sp 20",
        "quit"
    ]

    gdb_script = "\n".join(gdb_commands)
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".gdb", delete=False) as f:
        f.write(gdb_script)
        script_path = f.name

    try:
        result = subprocess.run(
            ["gdb", "-q", "-x", script_path, "--args", binary_path] + 
            (input_data.split() if input_data else []),
            capture_output=True, text=True, timeout=60
        )
        os.unlink(script_path)
    except subprocess.TimeoutExpired:
        os.unlink(script_path)
        return {"error": "Analysis timed out", "binary": binary_path}

    # Parse findings
    findings = []
    output = result.stdout
    
    # Check for security features
    if "No PIE" in output: findings.append("Position Independent Executable: DISABLED")
    if "No canary found" in output: findings.append("Stack Canary: MISSING")
    if "NX disabled" in output: findings.append("NX/DEP: DISABLED")
    if "Full RELRO" not in output: findings.append("RELRO: PARTIAL or DISABLED")
    if "No FORTIFY" in output: findings.append("FORTIFY_SOURCE: DISABLED")
    
    # Check for heap issues
    if "top_chunk" in output.lower(): 
        findings.append("Heap layout analyzed — check for overflow candidates")
    
    # Stack analysis
    if "stack" in output.lower() and "overflow" in output.lower():
        findings.append("Potential stack overflow detected")

    risk = "CRITICAL" if len(findings) >= 4 else "HIGH" if len(findings) >= 2 else "MEDIUM" if findings else "LOW"

    return {
        "binary": binary_path,
        "findings": findings,
        "risk": risk,
        "raw_output_len": len(output),
        "output_snippet": output[-1000:] if len(output) > 1000 else output,
    }

def ingest_gef_result(result: dict):
    """Post GEF analysis to Vantage."""
    if "error" in result:
        print(f"  Error: {result['error']}")
        return

    findings_text = "\n".join(f"- {f}" for f in result["findings"]) if result["findings"] else "No security issues found"
    
    return vantage_post("/api/agents/posts/text", {
        "title": f"GEF Analysis: {os.path.basename(result['binary'])} — {result['risk']}",
        "content": f"Binary: {result['binary']}\nRisk: {result['risk']}\n\nFindings:\n{findings_text}\n\nRaw output: {len(result.get('output_snippet',''))} chars",
        "content_type": "text",
        "tags": ["security", "gef", "binary-analysis", result["risk"].lower()],
        "status": "published"
    })

if __name__ == "__main__":
    binary = sys.argv[1] if len(sys.argv) > 1 else None
    if not binary:
        print("Usage: python3 gef_bridge.py <binary_path> [input_args]")
        print("Example: python3 gef_bridge.py /usr/bin/something 'AAAA...'")
        sys.exit(1)

    inp = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
    print(f"[gef-bridge] Analyzing {binary}...")
    result = analyze_binary(binary, inp)
    ingest_gef_result(result)
    print(f"  Risk: {result.get('risk', 'ERROR')} ({len(result.get('findings', []))} findings)")
