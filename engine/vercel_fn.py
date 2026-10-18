"""Vercel Python entry: transpile a workbook from multipart bytes in memory."""

from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler
from urllib.parse import unquote

from engine import transpile
from engine.schema import WorkbookParseError


def _json_bytes(payload: dict, status: int) -> tuple[int, bytes]:
    body = json.dumps(payload).encode("utf-8")
    return status, body


def extract_upload(content_type: str, body: bytes) -> tuple[str, bytes] | None:
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    if not message.is_multipart():
        return None
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if "name=\"file\"" not in disposition and "name=file" not in disposition:
            continue
        filename = part.get_filename() or "workbook.twb"
        payload = part.get_payload(decode=True) or b""
        return unquote(filename), payload
    return None


def transpile_payload(filename: str, payload: bytes) -> tuple[int, bytes]:
    name = filename.lower()
    if not name.endswith(".twb") and not name.endswith(".twbx"):
        return _json_bytes({"error": "Only .twb and .twbx workbooks are accepted."}, 400)
    if not payload:
        return _json_bytes({"error": "File is empty."}, 400)
    try:
        result = transpile(payload)
    except WorkbookParseError as exc:
        return _json_bytes({"error": exc.message}, 400)
    except Exception as exc:  # noqa: BLE001 — surface unexpected parse failures to the client
        return _json_bytes({"error": str(exc)}, 400)
    return 200, result.model_dump_json().encode("utf-8")


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — Vercel Python entrypoint
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type") or ""
        upload = extract_upload(content_type, body)
        if upload is None:
            status, payload = _json_bytes({"error": "Upload a .twb or .twbx file."}, 400)
        else:
            status, payload = transpile_payload(*upload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return
