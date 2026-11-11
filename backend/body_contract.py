"""One body contract for the guild surface: JSON or form, same route.

The guild routers used to declare their inputs as ``Form(...)`` parameters.
That made every one of them unusable from an MCP connector, which sends
JSON: 49 mutations answered 422 to any agent that was not a browser. The
routers now declare one Pydantic model per route, so OpenAPI (and the MCP
tools generated from it) describe a JSON body.

The UI and the agent-bridge still post forms, and they should not have to
change on the same day. This middleware rewrites a form body on a guild
path into the equivalent JSON body before routing, so the same route serves
both. It is scoped by path prefix: nothing outside the guild surface is
touched, and a multipart body carrying a file is passed through untouched,
since a file has no JSON equivalent.

Form semantics that matter:
  * a form value is always a string; the route model coerces ``"50"`` to
    ``50`` and ``"0.6"`` to ``0.6`` the way ``Form(...)`` did;
  * an empty form field means "not provided", so it is dropped and the
    model default applies, which is how FastAPI treated ``Form(None)``;
  * a repeated field becomes a list.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl

from starlette.datastructures import UploadFile
from starlette.requests import Request

#: Path prefixes whose routers accept JSON bodies and must also accept form.
GUILD_BODY_PREFIXES: tuple[str, ...] = ("/api/guilds/", "/api/guilds", "/api/guild/", "/api/chat/")

_FORM_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _wants_rewrite(scope: dict) -> str | None:
    """'urlencoded' | 'multipart' | None."""
    if scope.get("type") != "http" or scope.get("method", "") not in _FORM_METHODS:
        return None
    path = scope.get("path", "")
    if not any(path == p.rstrip("/") or path.startswith(p) for p in GUILD_BODY_PREFIXES):
        return None
    ct = ""
    for k, v in scope.get("headers", []):
        if k == b"content-type":
            ct = v.decode("latin-1").lower()
            break
    if ct.startswith("application/x-www-form-urlencoded"):
        return "urlencoded"
    if ct.startswith("multipart/form-data"):
        return "multipart"
    return None


def form_to_dict(pairs) -> dict:
    """Collapse (key, value) pairs the way a route model expects them."""
    out: dict = {}
    for k, v in pairs:
        if isinstance(v, str) and v == "":
            continue
        if k in out:
            if not isinstance(out[k], list):
                out[k] = [out[k]]
            out[k].append(v)
        else:
            out[k] = v
    return out


class FormToJSONMiddleware:
    """Pure ASGI: rewrite a form body on a guild path into JSON."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = _wants_rewrite(scope)
        if kind is None:
            await self.app(scope, receive, send)
            return

        # Buffer the whole body once; both branches need it.
        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        raw = b"".join(chunks)

        new_body: bytes | None = None
        if kind == "urlencoded":
            pairs = parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)
            new_body = json.dumps(form_to_dict(pairs)).encode()
        else:
            async def _replay():
                return {"type": "http.request", "body": raw, "more_body": False}
            form = await Request(scope, _replay).form()
            if not any(isinstance(v, UploadFile) for _, v in form.multi_items()):
                new_body = json.dumps(form_to_dict(form.multi_items())).encode()

        if new_body is None:
            body, headers = raw, list(scope["headers"])
        else:
            body = new_body
            headers = [(k, v) for k, v in scope["headers"] if k not in (b"content-type", b"content-length")]
            headers.append((b"content-type", b"application/json"))
            headers.append((b"content-length", str(len(body)).encode()))
        new_scope = dict(scope)
        new_scope["headers"] = headers

        sent = False

        async def _receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(new_scope, _receive, send)
