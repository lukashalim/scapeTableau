#!/usr/bin/env python3
"""Headless sidecar for the Electron desktop app.

Usage:
  python desktop/convert_cli.py convert INPUT OUTPUT.xlsx
  python desktop/convert_cli.py transpile INPUT --dialect ansi

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

from engine import transpile
from engine.extract import extract_to_xlsx
from engine.schema import ParseStatus, WorkbookParseError

CONVERT_ALLOWED = {".hyper", ".twbx", ".tdsx", ".twb"}
TRANSPILE_ALLOWED = {".twb", ".twbx", ".tdsx", ".hyper"}
DIALECTS = ("ansi", "snowflake", "postgres", "duckdb", "bigquery", "tsql")


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cmd_convert(source: Path, dest: Path) -> int:
    if not source.is_file():
        _emit({"ok": False, "error": f"File not found: {source}"})
        return 1
    if source.suffix.lower() not in CONVERT_ALLOWED:
        _emit(
            {
                "ok": False,
                "error": "Choose a .hyper, .twbx, .tdsx, or .twb file.",
            }
        )
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


def cmd_pbip(source: Path, dest: Path) -> int:
    from engine.pbip import transpile_to_pbip

    if not source.is_file():
        _emit({"ok": False, "error": f"File not found: {source}"})
        return 1
    try:
        zip_bytes, result = transpile_to_pbip(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zip_bytes)
    except WorkbookParseError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001
        _emit({"ok": False, "error": str(exc), "traceback": traceback.format_exc()})
        return 1
    _emit(
        {
            "ok": True,
            "output": str(dest),
            "workbook": result.workbook_name,
            "field_count": len(result.calculated_fields),
            "format": "pbip",
        }
    )
    return 0


def cmd_transpile(source: Path, dialect: str) -> int:
    if not source.is_file():
        _emit({"ok": False, "error": f"File not found: {source}"})
        return 1
    if source.suffix.lower() not in TRANSPILE_ALLOWED:
        _emit(
            {
                "ok": False,
                "error": "Choose a .twb, .twbx, .tdsx, or .hyper file.",
            }
        )
        return 1
    if dialect not in DIALECTS:
        _emit({"ok": False, "error": f"Unsupported dialect: {dialect}"})
        return 1

    try:
        result = transpile(source, dialect=dialect, target="sql")
    except WorkbookParseError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001
        _emit({"ok": False, "error": str(exc), "traceback": traceback.format_exc()})
        return 1

    skipped = [
        {
            "caption": f.caption,
            "status": f.status.value,
            "error": f.error,
        }
        for f in result.calculated_fields
        if f.status != ParseStatus.ok
    ]
    _emit(
        {
            "ok": True,
            "sql_script": result.sql_script,
            "workbook": result.workbook_name,
            "dialect": result.dialect,
            "skipped": skipped,
            "field_count": len(result.calculated_fields),
            "error": None,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BIEscape convert / transpile sidecar")
    sub = parser.add_subparsers(dest="command", required=True)

    p_convert = sub.add_parser("convert", help="Extract tables to Excel")
    p_convert.add_argument("input", type=Path)
    p_convert.add_argument("output", type=Path)

    p_transpile = sub.add_parser("transpile", help="Emit SQL script JSON")
    p_transpile.add_argument("input", type=Path)
    p_transpile.add_argument(
        "--dialect",
        default="ansi",
        choices=list(DIALECTS),
        help="SQL dialect (default: ansi)",
    )

    p_pbip = sub.add_parser("pbip", help="Emit Power BI Project (.pbip) zip")
    p_pbip.add_argument("input", type=Path)
    p_pbip.add_argument("output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "convert":
        return cmd_convert(args.input, args.output)
    if args.command == "transpile":
        return cmd_transpile(args.input, args.dialect)
    if args.command == "pbip":
        return cmd_pbip(args.input, args.output)
    _emit({"ok": False, "error": f"Unknown command: {args.command}"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
