"""ASGI entry for the Python Vercel project (in-memory transpile, no disk writes)."""

from __future__ import annotations

from engine.vercel_fn import extract_upload, transpile_payload

_JSON = [(b"content-type", b"application/json; charset=utf-8")]
_PATHS = {"/api/transpile", "/api/engine", "/transpile"}


async def app(scope: dict, receive, send) -> None:
    if scope.get("type") != "http":
        return
    path = scope.get("path") or "/"
    method = (scope.get("method") or "GET").upper()
    if method == "POST" and path in _PATHS:
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body") or b""
            more = bool(message.get("more_body"))
        headers = {key.decode("latin1"): value.decode("latin1") for key, value in scope.get("headers") or []}
        upload = extract_upload(headers.get("content-type", ""), body)
        if upload is None:
            status, payload = 400, b'{"error":"Upload a .twb or .twbx file."}'
        else:
            status, payload = transpile_payload(*upload)
        await send({"type": "http.response.start", "status": status, "headers": _JSON})
        await send({"type": "http.response.body", "body": payload})
        return
    await send({"type": "http.response.start", "status": 404, "headers": _JSON})
    await send({"type": "http.response.body", "body": b'{"error":"Not found"}'})
