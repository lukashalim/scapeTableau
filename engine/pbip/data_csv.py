"""Extract table CSVs into the PBIP ``Data/`` folder (sibling of the .pbip).

Small CSVs are also embedded in the M partition so Desktop can load rows
without a DataFolder path. Larger extracts stay as ``File.Contents(DataFolder & "\\file.csv")``.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

from engine.hyper import hyper_conversion_enabled, read_hyper_bytes
from engine.parser import Source, _is_zip, _read_bytes, is_hyper_bytes, list_archive_members
from engine.schema import DatasourceInfo, RelationInfo, TranspileResult

_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")
_REGION_EQ = re.compile(
    r"where\s+(?:orders\.)?region\s*=\s*'([^']+)'",
    re.IGNORECASE,
)

# Demo rows when a .twb has no packaged extract (website sample path).
_DEMO_SUPERSTORE_ROWS: list[dict[str, object]] = [
    {
        "Sales": 100,
        "Profit": 20,
        "Total Profit": 20,
        "Total Revenue": 100,
        "Discount": 0.1,
        "Quantity": 2,
        "Product Name": "Office Chair",
        "Customer Name": "Alice Adams",
        "Region": "West",
        "Internal Order Id": "W-1",
        "Category": "Furniture",
    },
    {
        "Sales": 80,
        "Profit": 10,
        "Total Profit": 10,
        "Total Revenue": 80,
        "Discount": 0,
        "Quantity": 1,
        "Product Name": "Desk Lamp",
        "Customer Name": "Bob Baker",
        "Region": "East",
        "Internal Order Id": "E-1",
        "Category": "Furniture",
    },
    {
        "Sales": 250,
        "Profit": 45,
        "Total Profit": 45,
        "Total Revenue": 250,
        "Discount": 0.05,
        "Quantity": 3,
        "Product Name": "Conference Table",
        "Customer Name": "Carol Chen",
        "Region": "West",
        "Internal Order Id": "W-2",
        "Category": "Furniture",
    },
]


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


def _parse_csv_bytes(payload: bytes) -> tuple[list[str], list[list[str]]]:
    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    headers = [h.strip() for h in rows[0]]
    body = [list(r) for r in rows[1:] if any((c or "").strip() for c in r)]
    return headers, body


# Tableau often declares Total Profit / Total Revenue while the extract only
# has Profit / Sales. Copy into a fully blank target so % of Total measures
# have a number to show.
_BLANK_COLUMN_FILL = {
    "total profit": "profit",
    "total revenue": "sales",
}


def _fill_blank_alias_columns(
    headers: list[str],
    rows: list[list[object]],
) -> list[str]:
    """Fill an all-blank target column from a sibling source column. Returns notes."""

    if not headers or not rows:
        return []
    index = {h.strip().lower(): i for i, h in enumerate(headers)}
    notes: list[str] = []
    for target, source in _BLANK_COLUMN_FILL.items():
        ti = index.get(target)
        si = index.get(source)
        if ti is None or si is None:
            continue
        if any(ti < len(row) and str(row[ti]).strip() for row in rows):
            continue
        filled = 0
        for row in rows:
            if si < len(row) and ti < len(row) and str(row[si]).strip():
                row[ti] = row[si]
                filled += 1
        if filled:
            notes.append(
                f"Filled blank '{headers[ti]}' from '{headers[si]}' ({filled} rows) "
                "so table-calc measures have numeric values."
            )
    return notes


def _align_rows(
    target_headers: list[str],
    source_headers: list[str],
    source_rows: list[list[str]],
    notes: list[str] | None = None,
) -> list[list[object]]:
    """Map source columns onto target headers by case-insensitive name."""

    if not target_headers:
        return [list(r) for r in source_rows]
    index = {h.strip().lower(): i for i, h in enumerate(source_headers) if h}
    out: list[list[object]] = []
    for row in source_rows:
        aligned: list[object] = []
        for header in target_headers:
            idx = index.get(header.strip().lower())
            aligned.append(row[idx] if idx is not None and idx < len(row) else "")
        out.append(aligned)
    extra = _fill_blank_alias_columns(target_headers, out)
    if notes is not None:
        notes.extend(extra)
    return out


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


def _primary_relation(ds: DatasourceInfo) -> RelationInfo | None:
    for rel in ds.relations:
        if rel.custom_sql or rel.table or rel.name:
            return rel
    return None


def _find_orders_csv(csv_members: dict[str, bytes]) -> bytes | None:
    for key in ("orders.csv", "data/orders.csv"):
        if key in csv_members:
            return csv_members[key]
    for key, payload in csv_members.items():
        if Path(key).name.lower() == "orders.csv":
            return payload
    return None


def _load_companion_orders(source: Source | None) -> bytes | None:
    """When converting a bare .twb, pull Orders.csv from the sibling .twbx sample."""

    path: Path | None = None
    if isinstance(source, Path):
        path = source
    elif isinstance(source, str) and (source.endswith(".twb") or source.endswith(".twbx")):
        path = Path(source)
    if path is None:
        return None
    if path.suffix.lower() == ".twbx":
        return None
    companion = path.with_suffix(".twbx")
    # Website / repo layout: samples/superstore_calcs.twb + .twbx
    candidates = [companion]
    repo_sample = Path(__file__).resolve().parents[2] / "samples" / "superstore_calcs.twbx"
    if path.stem.lower() == "superstore_calcs":
        candidates.append(repo_sample)
        web_sample = Path(__file__).resolve().parents[2] / "web" / "public" / "samples" / "superstore_calcs.twbx"
        candidates.append(web_sample)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            with zipfile.ZipFile(candidate) as zf:
                for name in zf.namelist():
                    if Path(name).name.lower() == "orders.csv":
                        return zf.read(name)
        except (OSError, zipfile.BadZipFile):
            continue
    return None


def _demo_rows_for_headers(headers: list[str]) -> list[list[object]]:
    if not headers:
        return []
    out: list[list[object]] = []
    for demo in _DEMO_SUPERSTORE_ROWS:
        out.append([demo.get(h, "") for h in headers])
    return out


def _materialize_custom_sql(
    ds: DatasourceInfo,
    *,
    headers: list[str],
    csv_members: dict[str, bytes],
    companion_orders: bytes | None,
) -> tuple[list[str], list[list[object]], str | None]:
    """Best-effort Custom SQL → CSV using packaged Orders.csv when SQL is simple."""

    rel = _primary_relation(ds)
    sql = (rel.custom_sql if rel else None) or ""
    if not sql.strip():
        return headers, [], None

    orders = _find_orders_csv(csv_members) or companion_orders
    if not orders:
        return headers, [], None

    src_headers, src_rows = _parse_csv_bytes(orders)
    if not src_headers:
        return headers, [], None

    match = _REGION_EQ.search(sql)
    filtered = src_rows
    note = None
    if match:
        region = match.group(1)
        # Find region column
        region_idx = next(
            (i for i, h in enumerate(src_headers) if h.strip().lower() == "region"),
            None,
        )
        if region_idx is not None:
            filtered = [
                r
                for r in src_rows
                if region_idx < len(r) and (r[region_idx] or "").strip().lower() == region.lower()
            ]
            note = (
                f"Data/{safe_csv_filename(ds.caption or ds.name or 'Table')}: "
                f"materialized Custom SQL filter region='{region}' from Orders.csv "
                f"({len(filtered)} rows)."
            )

    used_headers = headers or src_headers
    aligned = _align_rows(used_headers, src_headers, filtered)
    if not headers:
        used_headers = src_headers
        aligned = [list(r) for r in filtered]
    if note is None:
        note = (
            f"Data/{safe_csv_filename(ds.caption or ds.name or 'Table')}: "
            f"materialized Custom SQL from Orders.csv ({len(aligned)} rows)."
        )
    return used_headers, aligned, note


def collect_data_csvs(
    source: Source | None,
    result: TranspileResult,
    *,
    table_names: dict[str, str],
) -> tuple[dict[str, bytes], list[str], dict[str, list[str]]]:
    """Return ``Data/<table>.csv`` bytes, notes, and headers used per table name.

    Always writes a CSV per non-Parameters datasource (header-only if extract
    unavailable). Prefer embedded CSV/TSV from the package, then Hyper rows
    when ``tableauhyperapi`` is available. Custom SQL is materialized from
    Orders.csv when the query is a simple region filter.
    """

    notes: list[str] = []
    out: dict[str, bytes] = {}
    headers_by_table: dict[str, list[str]] = {}
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

    companion_orders = _load_companion_orders(source)
    if companion_orders and "orders.csv" not in csv_members:
        csv_members["orders.csv"] = companion_orders
        notes.append(
            "Loaded Orders.csv from companion .twbx sample for Data/ seeding "
            "(bare .twb has no packaged extract)."
        )

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
        # after the user points DataFolder at this directory.
        table_name = table_names.get(ds.name) or (ds.caption or ds.name or "Table")
        csv_name = safe_csv_filename(table_name)
        zip_path = f"Data/{csv_name}"
        headers = _headers_for_ds(ds)
        rel = _primary_relation(ds)

        # 1) Custom SQL → materialize from Orders.csv when possible
        if rel and rel.custom_sql:
            used_headers, rows, mat_note = _materialize_custom_sql(
                ds,
                headers=headers,
                csv_members=csv_members,
                companion_orders=companion_orders,
            )
            if not used_headers:
                used_headers = ["Column1"]
                notes.append(f"Data/{csv_name}: no columns discovered — wrote stub header.")
            if rows:
                out[zip_path] = _rows_to_csv_bytes(used_headers, rows)
                headers_by_table[table_name] = used_headers
                if mat_note:
                    notes.append(mat_note)
                continue
            # Fall through to demo / header-only with discovered headers
            headers = used_headers or headers

        # 2) Embedded CSV/TSV whose name matches table / connection filename
        matched_csv: bytes | None = None
        candidates = [
            csv_name.lower(),
            f"{table_name}.csv".lower(),
            f"{table_name}.tsv".lower(),
            "orders.csv",  # Sample Superstore often binds Orders.csv
        ]
        for conn in ds.connections:
            if conn.filename:
                candidates.append(Path(conn.filename).name.lower())
        for key in candidates:
            if key in csv_members:
                matched_csv = csv_members[key]
                break

        if matched_csv is not None:
            src_headers, src_rows = _parse_csv_bytes(matched_csv)
            used_headers = headers or src_headers
            if used_headers and src_rows:
                rows = (
                    _align_rows(used_headers, src_headers, src_rows, notes)
                    if src_headers
                    else [list(r) for r in src_rows]
                )
                out[zip_path] = _rows_to_csv_bytes(used_headers, rows)
                headers_by_table[table_name] = used_headers
                continue
            # Matched but no body — fall through to demo seed
            headers = used_headers or headers

        # 3) Hyper table rows (match via connection .hyper path)
        rows_h: list[list[object]] = []
        used_headers = headers
        table = _match_hyper_table(ds, hyper_tables_by_path)
        if table is not None:
            cols = list(getattr(table, "columns", None) or [])
            if cols:
                used_headers = cols
            rows_h = [list(r) for r in (getattr(table, "rows", None) or [])]

        if rows_h:
            if not used_headers:
                used_headers = ["Column1"]
            out[zip_path] = _rows_to_csv_bytes(used_headers, rows_h)
            headers_by_table[table_name] = used_headers
            continue

        # 4) Demo seed for known Sample Superstore-style schemas (website .twb)
        if not used_headers:
            used_headers = headers or ["Column1"]
            if used_headers == ["Column1"]:
                notes.append(f"Data/{csv_name}: no columns discovered — wrote stub header.")

        demo = _demo_rows_for_headers(used_headers)
        # Only seed demo when we recognize at least one demo column
        demo_keys = {k.lower() for row in _DEMO_SUPERSTORE_ROWS for k in row}
        recognizable = any(h.strip().lower() in demo_keys for h in used_headers)
        if demo and recognizable:
            out[zip_path] = _rows_to_csv_bytes(used_headers, demo)
            headers_by_table[table_name] = used_headers
            notes.append(
                f"Data/{csv_name}: seeded {len(demo)} demo rows "
                f"(no extract rows available for '{table_name}')."
            )
            continue

        out[zip_path] = _rows_to_csv_bytes(used_headers, [])
        headers_by_table[table_name] = used_headers
        notes.append(
            f"Data/{csv_name}: header-only (no extract rows available for "
            f"'{table_name}'). Point DataFolder at this Data directory after unzip."
        )

    return out, notes, headers_by_table
