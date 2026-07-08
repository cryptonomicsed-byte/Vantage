#!/usr/bin/env python3
"""
ParrotOS Tool Bridge — Run security tools from ParrotSec Docker container.
Provides: sqlmap, nikto, gobuster, nmap, ffuf, whatweb, wafw00f, commix
"""
import subprocess, sys

PARROT = ["docker", "exec", "ares-parrot"]

TOOLS = {
    "sqlmap":     ["sqlmap", "-u", "{target}", "--batch", "--random-agent", "--level=1"],
    "nikto":      ["nikto", "-h", "{target}", "-Tuning", "123"],
    "gobuster":   ["gobuster", "dir", "-u", "{target}", "-w", "/usr/share/wordlists/dirb/common.txt", "-q"],
    "nmap":       ["nmap", "-sV", "-T4", "--top-ports", "100", "{target}"],
    "ffuf":       ["ffuf", "-u", "{target}/FUZZ", "-w", "/usr/share/wordlists/dirb/common.txt", "-mc", "200,301"],
    "whatweb":    ["whatweb", "{target}", "--no-errors"],
    "wafw00f":    ["wafw00f", "{target}"],
    "commix":     ["commix", "--url", "{target}", "--batch"],
}

def run(tool, target):
    if tool not in TOOLS:
        print(f"Unknown tool: {tool}")
        print(f"Available: {list(TOOLS.keys())}")
        return ""
    
    cmd = PARROT + [arg.format(target=target) for arg in TOOLS[tool]]
    print(f"  [{tool}] {target}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        print(f"  [{tool}] {len(output.split(chr(10)))} lines output")
        return output
    except subprocess.TimeoutExpired:
        print(f"  [{tool}] timed out")
        return ""
    except Exception as e:
        print(f"  [{tool}] error: {e}")
        return ""

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: parrot_tools.py <tool> <target>")
        print(f"Tools: {list(TOOLS.keys())}")
        sys.exit(1)
    
    tool = sys.argv[1]
    target = sys.argv[2]
    output = run(tool, target)
    print(output[:2000] if output else "No output")
