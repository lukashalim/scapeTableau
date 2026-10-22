"""Load and fill PBIP/TMDL templates (placeholder substitution, not ad-hoc string hacks)."""

from __future__ import annotations

import json
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"

# Use ${NAME} so M/JSON literal braces (e.g. #table(..., {{1}})) are untouched.
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load_template(name: str) -> str:
    path = TEMPLATES / name
    return path.read_text(encoding="utf-8")


def render_template(name: str, mapping: dict[str, str]) -> str:
    text = load_template(name)

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in mapping:
            raise KeyError(f"Missing template placeholder ${{{key}}} in {name}")
        return mapping[key]

    return _PLACEHOLDER_RE.sub(repl, text)


def render_json_template(name: str, mapping: dict[str, str]) -> str:
    """Render a JSON template then pretty-print to validate structure."""

    filled = render_template(name, mapping)
    payload = json.loads(filled)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def tmdl_ident(name: str) -> str:
    """Quote a TMDL identifier when it contains spaces or special characters."""

    cleaned = (name or "").strip() or "Column"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cleaned):
        return cleaned
    escaped = cleaned.replace("'", "''")
    return f"'{escaped}'"


def tmdl_string(value: str) -> str:
    return '"' + (value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def indent_block(text: str, spaces: int = 1) -> str:
    prefix = "\t" * spaces
    lines = text.splitlines() or [""]
    return "\n".join(prefix + line if line.strip() else line for line in lines)
