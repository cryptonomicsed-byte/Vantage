"""A WebSocket to a path with no WebSocket route is closed, not a 500.

The frontend StaticFiles is mounted at "/", so it catches every unmatched
path. Starlette's StaticFiles asserts the scope is HTTP; a WebSocket used to
blow up there (AssertionError -> 500 + traceback, ~15/min in production).
"""
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from backend.main import HttpOnlyStaticFiles


def _app(cls, tmp_path):
    (tmp_path / "index.html").write_text("<p>hi</p>")
    app = Starlette()
    app.mount("/", cls(directory=str(tmp_path), html=True), name="frontend")
    return app


def test_bare_staticfiles_crashes_on_a_websocket(tmp_path):
    client = TestClient(_app(StaticFiles, tmp_path))
    with pytest.raises(AssertionError):
        with client.websocket_connect("/no/such/ws"):
            pass


def test_wrapped_staticfiles_closes_a_websocket_cleanly(tmp_path):
    client = TestClient(_app(HttpOnlyStaticFiles, tmp_path))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/no/such/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 1008


def test_wrapped_staticfiles_still_serves_http(tmp_path):
    r = TestClient(_app(HttpOnlyStaticFiles, tmp_path)).get("/")
    assert r.status_code == 200 and "hi" in r.text
