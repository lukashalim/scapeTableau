"""In-memory .twbx extract → openpyxl workbook bytes (no upload disk writes)."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import BinaryIO

from openpyxl import Workbook, load_workbook

from engine.parser import Source, _is_zip, _read_bytes, list_archive_members, parse_workbook
from engine.schema import ArchiveMember, CustomSqlEntry, ExtractInventory, WorkbookParseError

MAX_ROWS = 100_000
HYPER_NOTE = (
    "Hyper extracts are listed only. Excel conversion of .hyper is not run on Vercel "
    "and is skipped here so uploads stay in memory (tableauhyperapi needs a file path)."
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


def _hyperapi_available() -> bool:
    try:
        import tableauhyperapi  # noqa: F401
    except Exception:
        return False
    return True


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
    if hyper:
        notes.append(HYPER_NOTE)
        if _hyperapi_available():
            notes.append("tableauhyperapi is importable locally but Hyper files were not converted (no disk writes).")
    if not members:
        notes.append("This workbook has no packaged archive members (plain .twb).")
    elif not tabular and not custom_sql and hyper:
        notes.append("This package is Hyper-only for data files; download raw .hyper locally if needed.")
    elif not tabular and not custom_sql:
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
    if hyper:
        ws = next_ws("Hyper files")
        ws.append(["Path", "Size", "Note"])
        for item in hyper:
            ws.append([item.path, item.size, "Listed only — not converted"])
        converted.append(ws.title)

    if not first_sheet_used:
        default.title = _safe_sheet_name("Extract", used)
        default.append(["Note"])
        default.append(["No embedded CSV/TSV/XLS/XLSX tables were converted."])
        converted.append(default.title)

    inventory = build_inventory_from_parts(members, custom, converted_sheets=converted, extra_notes=notes)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), inventory
