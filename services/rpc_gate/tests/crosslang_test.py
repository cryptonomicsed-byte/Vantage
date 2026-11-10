#!/usr/bin/env python3
"""Cross-language check: api_key_pool.py (Python) <-> ares-rpc-gate (Go).

Uses a COPY of /opt/ares/api_key_pool.py re-pointed at temp config/state files,
a fake Helius + fake public RPC on loopback, and a freshly built gateway binary
on a free loopback port. Never touches the real /opt/ares state files.

    /opt/ares/venv/bin/python services/rpc_gate/tests/crosslang_test.py

Env: ARES_KEY_POOL_PY (default /opt/ares/api_key_pool.py), GO (default go).
"""
import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
POOL_PY = os.environ.get("ARES_KEY_POOL_PY", "/opt/ares/api_key_pool.py")
KEYS = ["xk-alpha-0001", "xk-bravo-0002", "xk-charlie-0003"]  # fake, short

hits = {}
behave = {}  # api-key -> (status, body)
lock = threading.Lock()


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        q = self.path.split("api-key=", 1)
        key = q[1].split("&")[0] if len(q) == 2 else ""
        with lock:
            hits[key] = hits.get(key, 0) + 1
        status, out = behave.get(key, (200, None))
        if out is None:
            out = json.dumps({"jsonrpc": "2.0", "result": "public" if not key else "helius", "id": body.get("id")})
        b = out.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def load_pool_copy(tmp, cfg_path, state_path):
    dst = os.path.join(tmp, "api_key_pool_copy.py")
    src = open(POOL_PY).read()
    new, n1 = re.subn(r'^CONFIG_PATH = .*$', f'CONFIG_PATH = {cfg_path!r}', src, flags=re.M)
    new, n2 = re.subn(r'^STATE_PATH = .*$', f'STATE_PATH = {state_path!r}', new, flags=re.M)
    assert n1 == 1 and n2 == 1, "could not re-point api_key_pool copy"
    open(dst, "w").write(new)
    spec = importlib.util.spec_from_file_location("api_key_pool_copy", dst)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.STATE_PATH == state_path and mod.CONFIG_PATH == cfg_path
    return mod


def http_json(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def main():
    tmp = tempfile.mkdtemp(prefix="rpcgate-xlang-")
    gate = None
    fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    fake_url = f"http://127.0.0.1:{fake.server_address[1]}"
    try:
        cfg_path = os.path.join(tmp, "pools.json")
        state_path = os.path.join(tmp, "state.json")
        json.dump({"helius": {"keys": KEYS, "assignments": {"early_buyer": 0}},
                   "birdeye": {"keys": ["bd-0001"], "assignments": {}}}, open(cfg_path, "w"))
        pool = load_pool_copy(tmp, cfg_path, state_path)

        binary = os.path.join(tmp, "ares-rpc-gate")
        subprocess.run([os.environ.get("GO", "go"), "build", "-o", binary, "."], cwd=SRC, check=True)
        port = free_port()
        env = dict(os.environ, RPC_GATE_ADDR=f"127.0.0.1:{port}", RPC_GATE_POOL_CONFIG=cfg_path,
                   RPC_GATE_POOL_STATE=state_path, HELIUS_RPC_BASE=fake_url + "/",
                   SOLANA_PUBLIC_RPC=fake_url + "/public", RPC_GATE_RELOAD_MS="0", PUBLIC_RPS="50")
        env.pop("RPC_GATE_TOKEN", None)
        gate = subprocess.Popen([binary], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                http_json(base + "/health")
                break
            except Exception:
                time.sleep(0.05)
        rpc = lambda i: http_json(base + "/", {"jsonrpc": "2.0", "id": i, "method": "getSlot"})

        # 1. Python -> Go: Python reports a hard quota on key 0; gateway must skip it.
        pool.report_error("helius", KEYS[0], 429, "Max usage reached")
        for i in range(6):
            st, _ = rpc(i)
            assert st == 200
        check(hits.get(KEYS[0], 0) == 0, "gateway skips key cooled by Python report_error")
        _, h = http_json(base + "/health")
        row0 = h["per_key"][0]
        check(row0["key"] == "…0001" and row0["cooling"] and 3590 <= row0["cooldown_remaining_s"] <= 3600,
              f"gateway /health shows Python cooldown masked ({row0})")

        # 2. Go -> Python: gateway sees 'max usage reached' on key 1 and writes the cooldown.
        behave[KEYS[1]] = (429, '{"jsonrpc":"2.0","error":{"code":-32429,"message":"max usage reached"},"id":1}')
        for i in range(4):
            rpc(i)
        rows = {r["index"]: r for r in pool.pool_status("helius")}
        check(rows[1]["cooling_down"] and 3590 <= rows[1]["cooldown_remaining_s"] <= 3600,
              f"Python pool_status sees gateway hard-quota cooldown on key 1 ({rows[1]['cooldown_remaining_s']}s)")
        check(rows[0]["cooling_down"], "Python's own key-0 entry preserved by gateway merge-write")
        check(pool.get_key("helius", "early_buyer") == KEYS[2], "Python get_key rotates to the only free key")
        raw = json.load(open(state_path))
        check(set(raw) == {f"helius:{KEYS[0]}", f"helius:{KEYS[1]}"} and all(isinstance(v, float) for v in raw.values()),
              "state file is flat {provider:key: float}")
        check(stat.S_IMODE(os.stat(state_path).st_mode) == 0o600, "state file mode 0600 after gateway write")

        # 3. Go plain 429 -> 120s, then everything capped -> public fallback.
        behave[KEYS[2]] = (429, '{"error":"Too Many Requests"}')
        st, body = rpc(99)
        rows = {r["index"]: r for r in pool.pool_status("helius")}
        check(rows[2]["cooling_down"] and 110 <= rows[2]["cooldown_remaining_s"] <= 120,
              f"plain 429 -> COOLDOWN_RATE_LIMIT seen by Python ({rows[2]['cooldown_remaining_s']}s)")
        check(st == 200 and body.get("result") == "public" and body.get("id") == 99,
              "all keys capped -> public fallback, verbatim reply")
        _, h = http_json(base + "/health")
        check(h["available"] == 0 and h["public_fallback_used"] >= 1, "health: 0 available, public_fallback_used>=1")

        # 4. Python clears key 0 with its own atomic writer -> gateway resumes using it.
        state = pool._state()
        state[f"helius:{KEYS[0]}"] = 0
        pool._save_json_atomic(state_path, state)
        before = hits.get(KEYS[0], 0)
        st, body = rpc(100)
        check(st == 200 and body.get("result") == "helius" and hits.get(KEYS[0], 0) == before + 1,
              "gateway re-reads state on change and resumes Python-cleared key")
        print("ALL CROSS-LANGUAGE CHECKS PASSED")
    finally:
        if gate:
            gate.terminate()
            try:
                gate.wait(5)
            except subprocess.TimeoutExpired:
                gate.kill()
        fake.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
