"""Extract table CSVs into the PBIP ``Data/`` folder (sibling of the .pbip).

M partitions bind via ``File.Contents(DataFolder & "\\file.csv")``; the user sets
``DataFolder`` to the absolute path of this folder after unzipping.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import BinaryIO

from engine.hyper import hyper_conversion_enabled, read_hyper_bytes
from engine.parser import Source, _is_zip, _read_bytes, is_hyper_bytes, list_archive_members
from engine.schema import DatasourceInfo, TranspileResult

_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_csv_filename(table_name: str) -> str:
    base = _SAFE.sub("_", (table_name or "Table").strip()).strip(" ._") or "Table"
    return f"{base}.csv"


def _rows_to_csv_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    if headers:
        writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if c is None else c for c in row])
    return buf.getvalue().encode("utf-8-sig")


def _headers_for_ds(ds: DatasourceInfo) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for field in ds.fields:
        if field.calculated:
            continue
        cap = (field.caption or field.name or "").strip("[]")
        if not cap or cap in seen:
            continue
        seen.add(cap)
        out.append(cap)
    return out


def _hyper_paths_for_ds(ds: DatasourceInfo) -> list[str]:
    out: list[str] = []
    for conn in ds.connections:
        for ref in (conn.dbname, conn.filename):
            if ref and str(ref).lower().endswith(".hyper"):
                out.append(str(ref).replace("\\", "/"))
    return out


def _match_hyper_table(ds: DatasourceInfo, hyper_tables_by_path: dict[str, list]) -> object | None:
    """Pick Hyper rows for a datasource using connection hyper path, then name match."""

    wanted = {p.lower() for p in _hyper_paths_for_ds(ds)}
    candidates: list = []
    for path, tables in hyper_tables_by_path.items():
        norm = path.replace("\\", "/").lower()
        base = Path(path).name.lower()
        if wanted and (norm in wanted or any(w.endswith(base) or base in w for w in wanted)):
            candidates.extend(tables)
    if not candidates and len(hyper_tables_by_path) == 1:
        candidates = list(next(iter(hyper_tables_by_path.values())))
    if not candidates:
        return None
    needles = {
        (ds.caption or "").strip().lower(),
        (ds.name or "").strip().lower(),
    }
    needles.discard("")
    for table in candidates:
        tname = (getattr(table, "table", None) or "").strip().lower()
        if tname in needles or any(n and n in tname for n in needles):
            return table
    return candidates[0]


def collect_data_csvs(
    source: Source | None,
    result: TranspileResult,
    *,
    table_names: dict[str, str],
) -> tuple[dict[str, bytes], list[str]]:
    """Return ``Data/<table>.csv`` bytes keyed by zip path, plus notes.

    Always writes a CSV per non-Parameters datasource (header-only if extract
    unavailable). Prefer embedded CSV/TSV from the package, then Hyper rows
    when ``tableauhyperapi`` is available.
    """

    notes: list[str] = []
    out: dict[str, bytes] = {}
    archive_bytes: bytes | None = None
    filename = ""
    if source is not None:
        try:
            archive_bytes, filename = _read_bytes(source)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Could not re-read workbook for Data/ CSVs: {exc}")
            archive_bytes = None

    # Index CSV/TSV members from the archive
    csv_members: dict[str, bytes] = {}
    hyper_payloads: list[tuple[str, bytes]] = []
    if archive_bytes and _is_zip(archive_bytes, filename):
        members = list_archive_members(archive_bytes)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            for member in members:
                lower = member.path.lower()
                try:
                    payload = zf.read(member.path)
                except KeyError:
                    continue
                if member.kind == "csv" or lower.endswith((".csv", ".tsv")):
                    csv_members[Path(member.path).name.lower()] = payload
                    csv_members[member.path.lower()] = payload
                elif member.kind == "hyper" or lower.endswith(".hyper"):
                    hyper_payloads.append((member.path, payload))
    elif archive_bytes and is_hyper_bytes(archive_bytes, filename):
        hyper_payloads.append((filename or "extract.hyper", archive_bytes))

    hyper_tables_by_path: dict[str, list] = {}
    if hyper_payloads and hyper_conversion_enabled():
        for path, payload in hyper_payloads:
            hr = read_hyper_bytes(payload)
            if hr.tables:
                hyper_tables_by_path[path] = list(hr.tables)
            elif hr.error:
                notes.append(f"Hyper {path}: {hr.error}")
    elif hyper_payloads and not hyper_conversion_enabled():
        notes.append(
            "Hyper extracts present but not converted here (Vercel / missing "
            "tableauhyperapi). Data/*.csv files contain headers only — refresh "
            "after exporting CSVs or use BIEscape Converter."
        )

    for ds in result.datasources:
        if (ds.name or "").lower() == "parameters":
            continue
        # Always emit a CSV stub for each table so DataFolder-backed M can refresh
        # after the user points DataFolder at this directory (live SQL tables ignore it).
        table_name = table_names.get(ds.name) or (ds.caption or ds.name or "Table")
        csv_name = safe_csv_filename(table_name)
        zip_path = f"Data/{csv_name}"
        headers = _headers_for_ds(ds)

        # 1) Embedded CSV/TSV whose name matches table / connection filename
        matched_csv: bytes | None = None
        candidates = [
            csv_name.lower(),
            f"{table_name}.csv".lower(),
            f"{table_name}.tsv".lower(),
        ]
        for conn in ds.connections:
            if conn.filename:
                candidates.append(Path(conn.filename).name.lower())
        for key in candidates:
            if key in csv_members:
                matched_csv = csv_members[key]
                break
        if matched_csv is not None:
            out[zip_path] = matched_csv
            continue

        # 2) Hyper table rows (match via connection .hyper path)
        rows: list[list[object]] = []
        used_headers = headers
        table = _match_hyper_table(ds, hyper_tables_by_path)
        if table is not None:
            cols = list(getattr(table, "columns", None) or [])
            if cols:
                used_headers = cols
            rows = [list(r) for r in (getattr(table, "rows", None) or [])]

        if not used_headers:
            used_headers = ["Column1"]
            notes.append(f"Data/{csv_name}: no columns discovered — wrote stub header.")

        out[zip_path] = _rows_to_csv_bytes(used_headers, rows)
        if not rows:
            notes.append(
                f"Data/{csv_name}: header-only (no extract rows available for "
                f"'{table_name}'). Point DataFolder at this Data directory after unzip."
            )

    if out:
        notes.append(
            "Set the semantic-model **DataFolder** parameter to the absolute path of the "
            "`Data` folder next to the `.pbip` (written into this zip). "
            "M partitions load `DataFolder & \"\\\\<table>.csv\"`."
        )
    return out, notes
