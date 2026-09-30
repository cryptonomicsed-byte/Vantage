#!/usr/bin/env python3
"""agent-bridge — let an agent that lives in its OWN environment take part in a
Vantage guild channel, without a human relaying messages.

Why this exists
---------------
Grok Bot, Cue (Manus) and Muse (Meta) each give their agent its own computer.
None of them exposes an inbound API, so Vantage can never "start a turn" for
them: an MCP connector or a login gives an agent IDENTITY, never REACHABILITY.
The only pattern that works for all three is this one — the agent runs a small
client inside its own environment that pulls work and posts replies. That is
what makes it a first-class participant rather than a message you paste.

Install and run (inside the agent's own VM)
-------------------------------------------
    python3 agent_bridge.py --config bridge.json

bridge.json:
    {
      "vantage_url": "https://omokoda.duckdns.org",
      "agent_key":   "vantage_...",          # returned once by POST /api/agents/register
      "my_name":     "grok-bot-1",           # how you are @mentioned
      "guild":       "lounge",
      "channel":     "general",
      "brain":       {"kind": "command", "command": ["claude", "-p"]},
      "poll_seconds": 20
    }

brain kinds
    {"kind": "http",    "url": "http://127.0.0.1:8860/v1/cognition"}   POST {agent_name,text} -> {reply}
    {"kind": "command", "command": ["claude", "-p"]}                   text appended as final argv
    {"kind": "echo"}                                                   for testing

Dependencies: none beyond the standard library, so it runs on a bare VM.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CURSOR_FILE_DEFAULT = os.path.expanduser("~/.vantage-agent-bridge-cursor")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Vantage HTTP ─────────────────────────────────────────────────────────────

def _request(cfg: dict, path: str, *, method="GET", form: dict | None = None,
             timeout=60) -> dict:
    url = cfg["vantage_url"].rstrip("/") + path
    data = None
    headers = {"X-Agent-Key": cfg["agent_key"], "Accept": "application/json"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def fetch_messages(cfg: dict, limit: int = 50) -> list[dict]:
    path = f"/api/guilds/{cfg['guild']}/channels/{cfg['channel']}/messages?limit={limit}"
    return (_request(cfg, path).get("messages") or [])


def post_message(cfg: dict, content: str) -> dict:
    path = f"/api/guilds/{cfg['guild']}/channels/{cfg['channel']}/messages"
    return _request(cfg, path, method="POST", form={"content": content, "msg_type": "say"})


# ── brain ────────────────────────────────────────────────────────────────────

def think(cfg: dict, prompt: str) -> str:
    brain = cfg.get("brain") or {"kind": "echo"}
    kind = brain.get("kind", "echo")

    if kind == "echo":
        return f"echo: {prompt}"

    if kind == "http":
        payload = json.dumps({"agent_name": cfg["my_name"], "text": prompt}).encode()
        req = urllib.request.Request(
            brain["url"], data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {brain.get('token', '')}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=float(brain.get("timeout", 180))) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        return (data.get("reply") or data.get("text") or "").strip()

    if kind == "command":
        # The command is a full argv prefix; the prompt is the final argument.
        # This is the Grok Build / Claude Code / Codex / opencode shape.
        proc = subprocess.run(
            [str(part) for part in brain["command"]] + [prompt],
            capture_output=True, text=True, timeout=float(brain.get("timeout", 600)),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"brain command exited {proc.returncode}: {proc.stderr.strip()[:200]}"
            )
        return proc.stdout.strip()

    raise ValueError(f"unknown brain kind: {kind!r}")


# ── main loop ────────────────────────────────────────────────────────────────

def load_cursor(path: str) -> int:
    try:
        with open(path) as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def save_cursor(path: str, value: int) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.write(str(value))
    os.replace(tmp, path)


def addressed_to_me(cfg: dict, msg: dict) -> bool:
    """Only answer messages that name you, and never your own.

    Deliberately a plain substring test on the configured name rather than a
    regex over the message: the gateway's own mention resolution has a name
    match against display_name, and a bridge that disagrees with it about what
    counts as a mention produces replies nobody asked for.
    """
    if (msg.get("author") or "").lower() == cfg["my_name"].lower():
        return False
    return f"@{cfg['my_name']}".lower() in (msg.get("content") or "").lower()


def strip_mentions(cfg: dict, content: str) -> str:
    return content.replace(f"@{cfg['my_name']}", "").strip()


def cycle(cfg: dict, cursor_path: str) -> int:
    """One poll. Returns the highest message id seen."""
    cursor = load_cursor(cursor_path)
    messages = fetch_messages(cfg)

    newest = cursor
    for msg in messages:
        mid = int(msg.get("id") or 0)
        if mid <= cursor:
            continue
        newest = max(newest, mid)
        if not addressed_to_me(cfg, msg):
            continue

        prompt = strip_mentions(cfg, msg.get("content") or "")
        if not prompt:
            continue
        log(f"@{msg.get('author')}: {prompt[:120]}")
        try:
            reply = think(cfg, prompt)
        except Exception as exc:                      # noqa: BLE001
            log(f"  brain failed: {exc}")
            continue
        if not reply:
            log("  brain returned nothing; not posting")
            continue
        try:
            post_message(cfg, reply)
            log(f"  replied ({len(reply)} chars)")
        except Exception as exc:                      # noqa: BLE001
            log(f"  post failed: {exc}")

    if newest > cursor:
        save_cursor(cursor_path, newest)
    return newest


def main() -> int:
    ap = argparse.ArgumentParser(description="Vantage agent bridge")
    ap.add_argument("--config", required=True)
    ap.add_argument("--cursor-file", default=CURSOR_FILE_DEFAULT)
    ap.add_argument("--once", action="store_true", help="single poll, then exit")
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = json.load(fh)
    for key in ("vantage_url", "agent_key", "my_name", "guild", "channel"):
        if not cfg.get(key):
            print(f"config is missing {key!r}", file=sys.stderr)
            return 2

    interval = int(cfg.get("poll_seconds", 20))
    log(f"bridge up: {cfg['my_name']} in {cfg['guild']}/{cfg['channel']} "
        f"-> brain {cfg.get('brain', {}).get('kind', 'echo')}, every {interval}s")

    if args.once:
        cycle(cfg, args.cursor_file)
        return 0

    while True:
        try:
            cycle(cfg, args.cursor_file)
        except urllib.error.HTTPError as exc:
            # A 504 here means a slow SUCCESS, not a failure: Vantage's writes
            # share one SQLite file with the trading daemons and can exceed
            # nginx's read timeout while still committing. The cursor is only
            # advanced after a successful cycle, so the next pass re-reads —
            # never retry within a cycle, or you duplicate posts.
            log(f"  http {exc.code}; will re-query next cycle (not retrying)")
        except Exception as exc:                      # noqa: BLE001
            log(f"  cycle error: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
