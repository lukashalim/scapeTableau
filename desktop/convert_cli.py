#!/usr/bin/env python3
"""Headless Hyper/TWBX/TDSX → Excel sidecar for the Electron desktop app.

Usage:
  python desktop/convert_cli.py INPUT OUTPUT.xlsx

Prints one JSON line to stdout and exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.extract import extract_to_xlsx
from engine.schema import WorkbookParseError

ALLOWED = {".hyper", ".twbx", ".tdsx"}


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BIEscape convert sidecar")
    parser.add_argument("input", type=Path, help="Path to .hyper / .twbx / .tdsx")
    parser.add_argument("output", type=Path, help="Destination .xlsx path")
    args = parser.parse_args(argv)

    source: Path = args.input
    dest: Path = args.output

    if not source.is_file():
        _emit({"ok": False, "error": f"File not found: {source}"})
        return 1
    if source.suffix.lower() not in ALLOWED:
        _emit({"ok": False, "error": "Choose a .hyper, .twbx, or .tdsx file."})
        return 1

    try:
        xlsx, inventory = extract_to_xlsx(source)
    except WorkbookParseError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001
        _emit({"ok": False, "error": str(exc), "traceback": traceback.format_exc()})
        return 1

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(xlsx)
    except OSError as exc:
        _emit({"ok": False, "error": f"Could not write {dest}: {exc}"})
        return 1

    _emit(
        {
            "ok": True,
            "output": str(dest),
            "sheets": list(inventory.converted_sheets),
            "notes": list(inventory.notes),
            "hyper_files": len(inventory.hyper_files),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
