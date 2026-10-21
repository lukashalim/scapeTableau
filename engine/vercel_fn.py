"""Vercel Python entry: transpile a workbook from multipart bytes in memory."""

from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler
from urllib.parse import unquote

from engine import extract_to_xlsx, transpile
from engine.pbip import build_pbip_zip
from engine.schema import WorkbookParseError

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PBIP_TYPE = "application/zip"


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


def extract_payload(filename: str, payload: bytes) -> tuple[int, bytes, str]:
    name = filename.lower()
    if not name.endswith(".twb") and not name.endswith(".twbx"):
        status, body = _json_bytes({"error": "Only .twb and .twbx workbooks are accepted."}, 400)
        return status, body, "application/json; charset=utf-8"
    if not payload:
        status, body = _json_bytes({"error": "File is empty."}, 400)
        return status, body, "application/json; charset=utf-8"
    try:
        xlsx, _inventory = extract_to_xlsx(payload)
    except WorkbookParseError as exc:
        status, body = _json_bytes({"error": exc.message}, 400)
        return status, body, "application/json; charset=utf-8"
    except Exception as exc:  # noqa: BLE001
        status, body = _json_bytes({"error": str(exc)}, 400)
        return status, body, "application/json; charset=utf-8"
    return 200, xlsx, XLSX_TYPE


def pbip_payload(filename: str, payload: bytes) -> tuple[int, bytes, str]:
    name = filename.lower()
    if not name.endswith(".twb") and not name.endswith(".twbx"):
        status, body = _json_bytes({"error": "Only .twb and .twbx workbooks are accepted."}, 400)
        return status, body, "application/json; charset=utf-8"
    if not payload:
        status, body = _json_bytes({"error": "File is empty."}, 400)
        return status, body, "application/json; charset=utf-8"
    try:
        result = transpile(payload)
        zip_bytes = build_pbip_zip(result)
    except WorkbookParseError as exc:
        status, body = _json_bytes({"error": exc.message}, 400)
        return status, body, "application/json; charset=utf-8"
    except Exception as exc:  # noqa: BLE001
        status, body = _json_bytes({"error": str(exc)}, 400)
        return status, body, "application/json; charset=utf-8"
    return 200, zip_bytes, PBIP_TYPE


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — Vercel Python entrypoint
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type") or ""
        path = self.path.split("?", 1)[0]
        upload = extract_upload(content_type, body)
        if upload is None:
            status, payload = _json_bytes({"error": "Upload a .twb or .twbx file."}, 400)
            ctype = "application/json; charset=utf-8"
        elif path.rstrip("/").endswith("/extract"):
            status, payload, ctype = extract_payload(*upload)
        elif path.rstrip("/").endswith("/pbip"):
            status, payload, ctype = pbip_payload(*upload)
        else:
            status, payload = transpile_payload(*upload)
            ctype = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        if ctype == XLSX_TYPE:
            self.send_header("Content-Disposition", 'attachment; filename="scapetableau-extract.xlsx"')
        elif ctype == PBIP_TYPE:
            self.send_header("Content-Disposition", 'attachment; filename="escapetableau.pbip.zip"')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return
