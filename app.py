"""ASGI entry for the Python Vercel project (in-memory transpile, no disk writes)."""

from __future__ import annotations

from engine.vercel_fn import (
    PBIP_TYPE,
    XLSX_TYPE,
    extract_payload,
    extract_upload,
    pbip_payload,
    transpile_payload,
)

_JSON = [(b"content-type", b"application/json; charset=utf-8")]
_XLSX = [
    (b"content-type", XLSX_TYPE.encode("ascii")),
    (b"content-disposition", b'attachment; filename="scapetableau-extract.xlsx"'),
]
_PBIP = [
    (b"content-type", PBIP_TYPE.encode("ascii")),
    (b"content-disposition", b'attachment; filename="escapetableau.pbip.zip"'),
]
_TRANSPILE_PATHS = {"/api/transpile", "/api/engine", "/transpile"}
_EXTRACT_PATHS = {"/api/extract", "/extract"}
_PBIP_PATHS = {"/api/pbip", "/pbip"}


async def app(scope: dict, receive, send) -> None:
    if scope.get("type") != "http":
        return
    path = scope.get("path") or "/"
    method = (scope.get("method") or "GET").upper()
    if method == "POST" and path in _TRANSPILE_PATHS | _EXTRACT_PATHS | _PBIP_PATHS:
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body") or b""
            more = bool(message.get("more_body"))
        headers = {key.decode("latin1"): value.decode("latin1") for key, value in scope.get("headers") or []}
        upload = extract_upload(headers.get("content-type", ""), body)
        if upload is None:
            status, payload, hdrs = 400, b'{"error":"Upload a .twb or .twbx file."}', _JSON
        elif path in _EXTRACT_PATHS:
            status, payload, ctype = extract_payload(*upload)
            hdrs = _XLSX if ctype == XLSX_TYPE else _JSON
        elif path in _PBIP_PATHS:
            status, payload, ctype = pbip_payload(*upload)
            hdrs = _PBIP if ctype == PBIP_TYPE else _JSON
        else:
            status, payload = transpile_payload(*upload)
            hdrs = _JSON
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": payload})
        return
    await send({"type": "http.response.start", "status": 404, "headers": _JSON})
    await send({"type": "http.response.body", "body": b'{"error":"Not found"}'})
