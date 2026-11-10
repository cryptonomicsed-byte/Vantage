"""CURVE_LISTENER_EXTERNAL gate + `python -m backend.curve_listener` entrypoint.

The API lifespan is not run by the test client (ASGITransport), so the gate is
tested through the function lifespan calls, plus a source check that lifespan
calls it. The entrypoint is tested as a real child process.
"""
import asyncio
import inspect
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend import curve_listener as cl

REPO = Path(__file__).resolve().parents[2]


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CURVE_")}
    env.update(CURVE_DB=str(tmp_path / "curve.db"), PYTHONPATH=str(REPO))
    env.update(extra)
    return env


@pytest.mark.parametrize("val,want", [(None, False), ("0", False), ("", False),
                                      ("1", True), ("true", True), ("ON", True)])
def test_external_flag(monkeypatch, val, want):
    if val is None:
        monkeypatch.delenv("CURVE_LISTENER_EXTERNAL", raising=False)
    else:
        monkeypatch.setenv("CURVE_LISTENER_EXTERNAL", val)
    assert cl.external() is want


def _run_gate(monkeypatch):
    from backend import main
    ran = []

    async def fake_loop():
        ran.append(1)

    monkeypatch.setattr(cl, "listener_loop", fake_loop)

    async def go():
        t = main._start_curve_listener()
        if t is not None:
            await t
        return t

    return asyncio.run(go()), ran


def test_gate_default_starts_in_process(monkeypatch):
    monkeypatch.delenv("CURVE_LISTENER_EXTERNAL", raising=False)
    task, ran = _run_gate(monkeypatch)
    assert task is not None and ran == [1]


def test_gate_external_does_not_start(monkeypatch):
    monkeypatch.setenv("CURVE_LISTENER_EXTERNAL", "1")
    task, ran = _run_gate(monkeypatch)
    assert task is None and ran == []


def test_lifespan_goes_through_gate_and_tolerates_none():
    from backend import main
    src = inspect.getsource(main.lifespan)
    assert "curve_listener_task = _start_curve_listener()" in src
    assert "create_task(_curve_loop())" not in src
    assert "shutdown_tasks = [t for t in shutdown_tasks if t is not None]" in src


def test_entrypoint_check(tmp_path):
    r = subprocess.run([sys.executable, "-m", "backend.curve_listener", "--check"], cwd=REPO,
                       env=_env(tmp_path, CURVE_LISTENER_ENABLED="1"), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    cfg = json.loads(r.stdout.strip().splitlines()[-1])
    assert cfg["enabled"] is True and cfg["source"] == "rpc"
    assert cfg["db"] == str(tmp_path / "curve.db") and (tmp_path / "curve.db").exists()


def test_entrypoint_bad_args_exit_nonzero(tmp_path):
    r = subprocess.run([sys.executable, "-m", "backend.curve_listener", "--nope"], cwd=REPO,
                       env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert r.returncode == 2


def test_entrypoint_disabled_exits_2(tmp_path):
    r = subprocess.run([sys.executable, "-m", "backend.curve_listener"], cwd=REPO,
                       env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert r.returncode == cl.EXIT_DISABLED
    assert "CURVE_LISTENER_ENABLED is off" in r.stderr


def test_entrypoint_unopenable_db_is_fatal(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    r = subprocess.run([sys.executable, "-m", "backend.curve_listener"], cwd=REPO,
                       env=_env(tmp_path, CURVE_LISTENER_ENABLED="1",
                                CURVE_DB=str(blocker / "sub" / "curve.db")),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == cl.EXIT_FATAL


# A child that runs the real main()/run_standalone() with listener_loop stubbed
# out, so no network is touched.
_STUB = """
import asyncio, sys
from backend import curve_listener as cl
async def fake():
    if sys.argv[1] == "boom":
        await asyncio.sleep(0.2)
        raise RuntimeError("boom")
    cl.STATE.update(running=True, connected=True)
    print("READY", flush=True)
    await asyncio.sleep(3600)
cl.listener_loop = fake
sys.exit(cl.main([]))
"""


def test_entrypoint_sigterm_exits_cleanly(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", _STUB, "wait"], cwd=REPO,
                         env=_env(tmp_path, CURVE_LISTENER_ENABLED="1"),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "READY"
        time.sleep(0.3)
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=15)
    finally:
        if p.poll() is None:
            p.kill()
    assert rc == 0, p.stderr.read()
    st = json.loads((tmp_path / "curve_state.json").read_text())
    assert st["running"] is False and st["connected"] is False and st["pid"] == p.pid


def test_entrypoint_fatal_error_exits_1(tmp_path):
    r = subprocess.run([sys.executable, "-c", _STUB, "boom"], cwd=REPO,
                       env=_env(tmp_path, CURVE_LISTENER_ENABLED="1"),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == cl.EXIT_FATAL
    assert "fatal error" in r.stderr


def test_coverage_reads_external_state_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "curve.db"))
    monkeypatch.setenv("CURVE_LISTENER_EXTERNAL", "1")
    now = time.time()
    (tmp_path / "curve_state.json").write_text(json.dumps(
        {"running": True, "connected": True, "last_event_at": now - 2, "written_at": now - 1}))
    out = cl.coverage()
    assert out["external"] is True and out["state"]["connected"] is True
    assert 1 <= out["last_event_age_s"] < 10 and out["state_age_s"] < 10


def test_coverage_default_shape_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("CURVE_DB", str(tmp_path / "curve.db"))
    monkeypatch.delenv("CURVE_LISTENER_EXTERNAL", raising=False)
    out = cl.coverage()
    assert "external" not in out and "state_age_s" not in out
    assert out["state"] == dict(cl.STATE)
