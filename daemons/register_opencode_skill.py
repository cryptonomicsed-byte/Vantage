#!/usr/bin/env python3
import json, urllib.request, urllib.error

KEY = open("/opt/ares/.vantage_key").read().strip()
BASE = "http://localhost:8001/api/collectives/skills"
body = {
    "name": "opencode",
    "description": "Run the Opencode AI coding agent on a task (optional repo). Returns agent output + git diff. OSS-as-slash-command wrap for /Opencode.",
    "input_schema": {
        "base_url": "http://127.0.0.1:9879",
        "write": True,
        "routes": [
            {"method": "POST", "path": "/run"},
            {"method": "GET", "path": "/run/{run_id}"},
            {"method": "GET", "path": "/health"},
        ],
        "params": {"task": "string (required)", "clone_url": "string?", "model": "string?", "agent": "string?"},
    },
    "runtime": "external-http",
}

def call(method, url, data=None):
    d = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=d, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Agent-Key", KEY)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

s, out = call("POST", BASE, body)
print("register status:", s, "->", out[:200])
s2, out2 = call("GET", BASE + "?skill=opencode")
try:
    d = json.loads(out2)
    d = d if isinstance(d, list) else d.get("skills", [d])
    for sk in d:
        if isinstance(sk, dict) and sk.get("name") == "opencode":
            print("LISTED:", sk.get("name"), "|", sk.get("runtime"), "|", (sk.get("description") or "")[:60])
except Exception as e:
    print("list:", s2, out2[:200])
