"""In-memory .twbx extract → openpyxl workbook bytes (optional local Hyper conversion)."""

from __future__ import annotations

import csv
import io
import os
import zipfile
from pathlib import Path
from typing import BinaryIO

from openpyxl import Workbook, load_workbook

from engine.hyper import (
    MAX_ROWS,
    hyper_conversion_enabled,
    read_hyper_bytes,
    sheet_title_for,
)
from engine.parser import Source, _is_zip, _read_bytes, list_archive_members, parse_workbook
from engine.schema import ArchiveMember, CustomSqlEntry, ExtractInventory, WorkbookParseError

HYPER_NOTE_VERCEL = (
    "Hyper extracts are listed only on Vercel / the hosted site. "
    "tableauhyperapi (hyperd) is too large for serverless; convert locally with the CLI or Streamlit."
)
HYPER_NOTE_LOCAL_OK = (
    "Hyper extracts were converted locally via optional tableauhyperapi "
    "(temp file for hyperd, deleted after read; 100k row cap)."
)
HYPER_NOTE_LOCAL_SKIP = (
    "Hyper extracts are listed only. Install optional tableauhyperapi to convert locally "
    "(not used when VERCEL is set)."
)


def _safe_sheet_name(name: str, used: set[str]) -> str:
    cleaned = "".join("_" if ch in r'[]:*?/\\' else ch for ch in name).strip() or "Sheet"
    cleaned = cleaned[:31]
    candidate = cleaned
    n = 1
    while candidate in used:
        suffix = f"_{n}"
        candidate = (cleaned[: 31 - len(suffix)] + suffix)[:31]
        n += 1
    used.add(candidate)
    return candidate


def _write_rows(ws, rows: list[list[object]]) -> None:
    for row in rows[:MAX_ROWS]:
        ws.append(list(row))


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _csv_rows(data: bytes, *, delimiter: str) -> list[list[object]]:
    text = _decode_text(data)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[list[object]] = []
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        rows.append(list(row))
    return rows


def _xlsx_rows(data: bytes) -> dict[str, list[list[object]]]:
    bio = io.BytesIO(data)
    try:
        wb = load_workbook(bio, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — old .xls and corrupt books
        raise WorkbookParseError(f"Unable to read spreadsheet: {exc}") from exc
    sheets: dict[str, list[list[object]]] = {}
    try:
        for ws in wb.worksheets:
            rows: list[list[object]] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS:
                    break
                rows.append(["" if cell is None else cell for cell in row])
            sheets[ws.title] = rows
    finally:
        wb.close()
    return sheets


def build_inventory_from_parts(
    members: list[ArchiveMember],
    custom_sql: list[CustomSqlEntry],
    *,
    converted_sheets: list[str] | None = None,
    extra_notes: list[str] | None = None,
) -> ExtractInventory:
    notes = list(extra_notes or [])
    hyper = [m for m in members if m.kind == "hyper"]
    tabular = [m for m in members if m.kind in {"csv", "xlsx"}]
    if not members:
        notes.append("This workbook has no packaged archive members (plain .twb).")
    elif not tabular and not custom_sql and hyper and not any(
        s for s in (converted_sheets or []) if "Extract" in s or "hyper" in s.lower()
    ):
        notes.append("This package is Hyper-only for data files; convert locally or download raw .hyper.")
    elif not tabular and not custom_sql and not hyper:
        notes.append("No embedded CSV/TSV/XLS/XLSX tables were found to convert.")
    return ExtractInventory(
        archive_members=members,
        tabular_files=tabular,
        hyper_files=hyper,
        custom_sql=custom_sql,
        converted_sheets=list(converted_sheets or []),
        notes=notes,
    )


def build_inventory(source: Source | None = None, *, workbook=None) -> ExtractInventory:
    parsed = workbook if workbook is not None else parse_workbook(source)  # type: ignore[arg-type]
    custom = [
        CustomSqlEntry(name=rel.name, datasource=ds.caption or ds.name, sql=rel.custom_sql)
        for ds in parsed.datasources
        for rel in ds.relations
        if rel.custom_sql
    ]
    return build_inventory_from_parts(list(parsed.archive_members), custom)


def extract_to_xlsx(source: str | Path | bytes | BinaryIO) -> tuple[bytes, ExtractInventory]:
    data, filename = _read_bytes(source)
    if not data:
        raise WorkbookParseError("Workbook is empty")
    parsed = parse_workbook(data)

    members: list[ArchiveMember] = list(parsed.archive_members)
    zip_bytes = data if _is_zip(data, filename) else None
    if zip_bytes is not None and not members:
        members = list_archive_members(zip_bytes)

    custom = [
        CustomSqlEntry(name=rel.name, datasource=ds.caption or ds.name, sql=rel.custom_sql)
        for ds in parsed.datasources
        for rel in ds.relations
        if rel.custom_sql
    ]

    wb = Workbook()
    used: set[str] = set()
    default = wb.active
    assert default is not None
    converted: list[str] = []
    notes: list[str] = []
    first_sheet_used = False

    def next_ws(title: str):
        nonlocal first_sheet_used
        name = _safe_sheet_name(title, used)
        if not first_sheet_used:
            default.title = name
            first_sheet_used = True
            return default
        return wb.create_sheet(name)

    if zip_bytes is not None:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in members:
                if member.kind not in {"csv", "xlsx"}:
                    continue
                payload = zf.read(member.path)
                stem = Path(member.path).name
                lower = member.path.lower()
                try:
                    if lower.endswith(".csv"):
                        rows = _csv_rows(payload, delimiter=",")
                        ws = next_ws(stem)
                        _write_rows(ws, rows)
                        converted.append(ws.title)
                    elif lower.endswith(".tsv"):
                        rows = _csv_rows(payload, delimiter="\t")
                        ws = next_ws(stem)
                        _write_rows(ws, rows)
                        converted.append(ws.title)
                    else:
                        sheets = _xlsx_rows(payload)
                        for sheet_name, rows in sheets.items():
                            ws = next_ws(f"{Path(stem).stem}_{sheet_name}")
                            _write_rows(ws, rows)
                            converted.append(ws.title)
                except WorkbookParseError as exc:
                    notes.append(f"{member.path}: {exc.message}")

    if custom:
        ws = next_ws("Custom SQL")
        ws.append(["Datasource", "Relation", "SQL"])
        for entry in custom:
            ws.append([entry.datasource or "", entry.name or "", entry.sql])
        converted.append(ws.title)

    hyper = [m for m in members if m.kind == "hyper"]
    hyper_converted = False
    if hyper:
        can_convert = hyper_conversion_enabled()
        index_rows: list[list[object]] = [["Path", "Size", "Note"]]
        if zip_bytes is not None and can_convert:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for item in hyper:
                    payload = zf.read(item.path)
                    result = read_hyper_bytes(payload)
                    if result.error and not result.tables:
                        index_rows.append([item.path, item.size, f"Listed only — {result.error}"])
                        notes.append(f"{item.path}: {result.error}")
                        continue
                    for table in result.tables:
                        title = sheet_title_for(item.path, table.schema, table.table)
                        ws = next_ws(title)
                        if table.columns:
                            ws.append(list(table.columns))
                        _write_rows(ws, table.rows)
                        converted.append(ws.title)
                        hyper_converted = True
                        note = f"Converted ({table.schema}.{table.table})"
                        if table.truncated:
                            note += f"; truncated at {MAX_ROWS} rows"
                            notes.append(f"{item.path}: truncated at {MAX_ROWS} rows")
                        index_rows.append([item.path, item.size, note])
                    if result.error:
                        notes.append(f"{item.path}: {result.error}")
        else:
            for item in hyper:
                if can_convert:
                    index_rows.append([item.path, item.size, "Listed only — not packaged in zip"])
                else:
                    index_rows.append(
                        [
                            item.path,
                            item.size,
                            "Listed only — not converted (Vercel or missing tableauhyperapi)",
                        ]
                    )
        ws = next_ws("Hyper files")
        for row in index_rows:
            ws.append(row)
        converted.append(ws.title)
        if hyper_converted:
            notes.append(HYPER_NOTE_LOCAL_OK)
        elif can_convert:
            notes.append(HYPER_NOTE_LOCAL_SKIP)
        else:
            notes.append(HYPER_NOTE_VERCEL if os.environ.get("VERCEL") else HYPER_NOTE_LOCAL_SKIP)

    if not first_sheet_used:
        default.title = _safe_sheet_name("Extract", used)
        default.append(["Note"])
        default.append(["No embedded CSV/TSV/XLS/XLSX tables were converted."])
        converted.append(default.title)

    inventory = build_inventory_from_parts(members, custom, converted_sheets=converted, extra_notes=notes)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), inventory
