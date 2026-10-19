"""Redact connection passwords before JSON or Excel output."""

from __future__ import annotations

import re

_CONNECT_SECRET = re.compile(
    r"(?i)(\b(?:PWD|Password|Pwd)\b)\s*=\s*([^;]*)",
)


def redact_connect_fragment(value: str | None) -> str | None:
    if value is None:
        return None
    return _CONNECT_SECRET.sub(r"\1=***", value)


def redact_password_attr(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    return "***"
