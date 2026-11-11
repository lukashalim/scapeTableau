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
from engine.schema import DatasourceInfo, JoinInfo, RelationInfo, TranspileResult

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


def _hdr_key(name: str) -> str:
    """Header identity ignoring case, spaces and punctuation (order_id == Order Id)."""

    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _source_index(target: str, index: dict[str, int]) -> int | None:
    key = _hdr_key(target)
    if key in index:
        return index[key]
    # Tableau read a doubled header ('Ship Mode Ship Mode'); the sibling file has 'Ship Mode'.
    half = len(key) // 2
    if half and len(key) % 2 == 0 and key[:half] == key[half:]:
        return index.get(key[:half])
    return None


def _align_rows(
    target_headers: list[str],
    source_headers: list[str],
    source_rows: list[list[str]],
    notes: list[str] | None = None,
) -> list[list[object]]:
    """Map source columns onto target headers by normalised name."""

    if not target_headers:
        return [list(r) for r in source_rows]
    index: dict[str, int] = {}
    for i, h in enumerate(source_headers):
        if h:
            index.setdefault(_hdr_key(h), i)
    positions = [_source_index(h, index) for h in target_headers]
    out: list[list[object]] = []
    for row in source_rows:
        out.append(
            [row[i] if i is not None and i < len(row) else "" for i in positions]
        )
    extra = _fill_blank_alias_columns(target_headers, out)
    if notes is not None:
        notes.extend(extra)
    return out


def _headers_for_ds(ds: DatasourceInfo) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for field in ds.fields:
        if field.calculated or (field.datatype or "").lower() == "table":
            continue
        if "__tableau_internal_object_id__" in (field.name or ""):
            continue
        cap = (field.caption or field.name or "").strip("[]")
        key = _hdr_key(cap)
        if not cap or key in seen:
            continue
        seen.add(key)
        out.append(cap)
    return out


_TEXT_SUFFIXES = {".csv", ".tsv", ".txt"}
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
_DATA_FILE_SUFFIXES = _TEXT_SUFFIXES | _EXCEL_SUFFIXES


def _real_file_ref(ds: DatasourceInfo) -> str | None:
    """Name of the CSV/Excel file a datasource points at (None for synthetic fixtures)."""

    for conn in ds.connections:
        name = Path((conn.filename or "").replace("\\", "/")).name
        if name and Path(name).suffix.lower() in _DATA_FILE_SUFFIXES:
            return name
    rel = _primary_relation(ds)
    for ref in ((rel.name if rel else None), (rel.table if rel else None)):
        text = (ref or "").strip("[]'\" ")
        if Path(text).suffix.lower() in _TEXT_SUFFIXES:
            return Path(text).name
    return None


def _references_hyper(ds: DatasourceInfo) -> bool:
    """True when this datasource's rows live in a Hyper extract, not a text file."""

    for conn in ds.connections:
        kind = (conn.class_name or "").lower()
        refs = " ".join(
            str(part or "") for part in (conn.dbname, conn.filename)
        ).lower()
        if kind == "hyper" or ".hyper" in refs:
            return True
    return False


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




def _workbook_dir(source: Source | None) -> Path | None:
    """Directory of a .twb/.twbx path. Bytes and file objects have no siblings."""

    path: Path | None = None
    if isinstance(source, Path):
        path = source
    elif isinstance(source, str):
        candidate = Path(source)
        if candidate.suffix.lower() in {".twb", ".twbx", ".tdsx"}:
            path = candidate
    if path is None or not path.is_file():
        return None
    return path.parent


def _norm_stem(name: str) -> str:
    stem = re.sub(r"\d+$", "", re.sub(r"[^a-z0-9]", "", name.lower()))
    return stem


def _relation_sheet(ds: DatasourceInfo) -> str | None:
    """Tableau Excel relations look like ``['HR data$']``."""

    rel = _primary_relation(ds)
    raw = ((rel.table if rel else None) or "").strip()
    raw = raw.strip("[]").strip("'\"").strip()
    if raw.endswith("$"):
        raw = raw[:-1].strip()
    return raw or None


def _xls_sheet_csv(path: Path, sheet: str | None) -> bytes | None:
    """First matching worksheet of a legacy .xls book (needs the optional xlrd package)."""

    try:
        import xlrd
    except ImportError:
        return None
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except Exception:  # noqa: BLE001 - unreadable/corrupt book: fall back to header-only
        return None
    try:
        names = book.sheet_names()
        target = None
        if sheet:
            want = sheet.strip().lower()
            target = next((n for n in names if n.strip().lower() == want), None)
        target = target or (names[0] if names else None)
        if target is None:
            return None
        sh = book.sheet_by_name(target)
        if sh.nrows == 0:
            return None
        rows = [
            ["" if c in (None, "") else c for c in sh.row_values(r)] for r in range(sh.nrows)
        ]
        return _rows_to_csv_bytes([str(h) for h in rows[0]], rows[1:])
    finally:
        book.release_resources()


def _excel_sheet_csv(path: Path, sheet: str | None) -> bytes | None:
    """First matching worksheet of a sibling .xlsx/.xls, as CSV bytes."""

    if path.suffix.lower() == ".xls":
        return _xls_sheet_csv(path, sheet)
    try:
        from openpyxl import load_workbook
    except ImportError:
        return None
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        names = list(wb.sheetnames)
        target = None
        if sheet:
            want = sheet.strip().lower()
            for name in names:
                if name.strip().lower() == want:
                    target = name
                    break
        target = target or (names[0] if names else None)
        if target is None:
            return None
        rows_iter = wb[target].iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            return None
        headers = ["" if cell is None else str(cell) for cell in header_row]
        body = [list(row) for row in rows_iter]
        return _rows_to_csv_bytes(headers, body)
    finally:
        wb.close()


_MAX_JOINED_ROWS = 200_000


def _exact_key(name: str) -> str:
    """Case-insensitive header key that keeps punctuation ('% Change' != 'Change')."""

    return (name or "").strip().lower()


def _sheet_name(rel: RelationInfo) -> str:
    raw = (rel.table or rel.name or "").strip().strip("[]").strip("'\"").strip()
    return raw[:-1].strip() if raw.endswith("$") else raw


def _joined_excel_csv(
    book: Path,
    ds: DatasourceInfo,
    joins: list[JoinInfo],
    headers: list[str],
) -> tuple[bytes | None, str]:
    """Materialise a Tableau join of two sheets of one Excel book as a single flat CSV.

    Tableau joins ``<relation type='table'>`` sheets at query time; the Power BI model here
    keeps one flat table per datasource, so the join is evaluated while writing Data/.
    Returns ``(csv bytes | None, note)``; ``None`` means the join was not applied.
    """

    tables = [r for r in ds.relations if r.type == "table"]
    if len(tables) < 2:
        return None, ""
    clause = next(
        (
            j for j in joins
            if (j.left_datasource in {ds.caption, ds.name})
            and (j.right_datasource in {ds.caption, ds.name})
        ),
        None,
    )
    if clause is None:
        return None, ""
    left_rel, right_rel = tables[0], tables[1]
    left_csv = _excel_sheet_csv(book, _sheet_name(left_rel))
    right_csv = _excel_sheet_csv(book, _sheet_name(right_rel))
    if not left_csv or not right_csv:
        return None, ""
    l_head, l_rows = _parse_csv_bytes(left_csv)
    r_head, r_rows = _parse_csv_bytes(right_csv)
    l_index = {_exact_key(h): i for i, h in enumerate(l_head)}
    r_index = {_exact_key(h): i for i, h in enumerate(r_head)}
    lk = l_index.get(_exact_key(clause.left_column))
    rk = r_index.get(_exact_key(clause.right_column))
    if lk is None or rk is None:
        return None, ""
    by_key: dict[str, list[list[str]]] = {}
    for row in r_rows:
        by_key.setdefault(row[rk] if rk < len(row) else "", []).append(row)
    total = 0
    for row in l_rows:
        key = row[lk] if lk < len(row) else ""
        total += max(len(by_key.get(key, [])), 1 if (clause.join_type or "").lower() == "left" else 0)
    if total > _MAX_JOINED_ROWS:
        return None, (
            f"join of '{_sheet_name(left_rel)}' and '{_sheet_name(right_rel)}' on "
            f"[{clause.left_column}] would create {total:,} rows; only the first sheet was loaded."
        )

    suffix = f" ({right_rel.name})".lower() if right_rel.name else ""

    def source_for(header: str) -> tuple[str, int] | None:
        key = _exact_key(header)
        if key in l_index:
            return "l", l_index[key]
        stripped = header
        if suffix and header.lower().endswith(suffix):
            stripped = header[: -len(suffix)]
        skey = _exact_key(stripped)
        if skey in r_index:
            return "r", r_index[skey]
        return None

    sources = [source_for(h) for h in headers]
    keep_left = (clause.join_type or "inner").lower() in {"left", "full"}
    blank_right: list[str] = [""] * len(r_head)
    out_rows: list[list[object]] = []
    for lrow in l_rows:
        matches = by_key.get(lrow[lk] if lk < len(lrow) else "", [])
        if not matches and keep_left:
            matches = [blank_right]
        for rrow in matches:
            line: list[object] = []
            for src in sources:
                if src is None:
                    line.append("")
                    continue
                side, idx = src
                data = lrow if side == "l" else rrow
                line.append(data[idx] if idx < len(data) else "")
            out_rows.append(line)
    note = (
        f"joined '{_sheet_name(left_rel)}' and '{_sheet_name(right_rel)}' on "
        f"[{clause.left_column}] = [{clause.right_column}] ({clause.join_type or 'inner'}) "
        f"into {len(out_rows):,} rows."
    )
    return _rows_to_csv_bytes(headers, out_rows), note


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
    # Calc fixture only: samples/superstore_calcs.twb next to its .twbx.
    candidates = [companion]
    repo_sample = Path(__file__).resolve().parents[2] / "samples" / "superstore_calcs.twbx"
    if path.stem.lower() == "superstore_calcs":
        candidates.append(repo_sample)
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

    sibling_dir = _workbook_dir(source)
    if sibling_dir is not None:
        for child in sibling_dir.iterdir():
            if child.is_file() and child.suffix.lower() in _TEXT_SUFFIXES:
                csv_members.setdefault(child.name.lower(), child.read_bytes())

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

        if matched_csv is None and sibling_dir is not None:
            for conn in ds.connections:
                fname = conn.filename or ""
                if Path(fname).suffix.lower() not in _EXCEL_SUFFIXES:
                    continue
                book = sibling_dir / Path(fname).name
                if not book.is_file():
                    continue
                joined_csv, join_note = _joined_excel_csv(
                    book, ds, list(getattr(result, "joins", None) or []), headers
                )
                if joined_csv:
                    matched_csv = joined_csv
                    notes.append(f"Data/{csv_name}: {join_note}")
                    break
                if join_note:
                    notes.append(f"Data/{csv_name}: {join_note}")
                sheet_csv = _excel_sheet_csv(book, _relation_sheet(ds))
                if sheet_csv:
                    matched_csv = sheet_csv
                    notes.append(
                        f"Data/{csv_name}: loaded '{_relation_sheet(ds) or 'first sheet'}' "
                        f"from sibling workbook {book.name}."
                    )
                    break
                if book.suffix.lower() == ".xls":
                    notes.append(
                        f"Data/{csv_name}: sibling {book.name} is .xls and could not be read "
                        "(install xlrd, or save it as .xlsx)."
                    )

        if matched_csv is None and sibling_dir is not None:
            needles = [
                _norm_stem(ds.caption or ""),
                _norm_stem(ds.name or ""),
            ]
            for conn in ds.connections:
                if conn.filename:
                    needles.append(_norm_stem(Path(conn.filename).stem))
            needles = [n for n in needles if len(n) >= 8]
            best_len = 0
            for child in sibling_dir.iterdir():
                if not child.is_file() or child.suffix.lower() not in {".csv", ".tsv"}:
                    continue
                stem = _norm_stem(child.stem)
                if len(stem) < 8:
                    continue
                for needle in needles:
                    if stem == needle or stem.startswith(needle) or needle.startswith(stem):
                        if len(stem) > best_len:
                            best_len = len(stem)
                            matched_csv = csv_members.get(child.name.lower())
            if matched_csv is not None and best_len:
                notes.append(
                    f"Data/{csv_name}: matched a sibling CSV by name for '{table_name}'."
                )

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

        # Hyper extracts are not the demo fixture. Leave headers until tableauhyperapi
        # can read the .hyper file (local only). Do not invent Superstore rows.
        if _references_hyper(ds):
            if not used_headers:
                used_headers = headers or ["Column1"]
            out[zip_path] = _rows_to_csv_bytes(used_headers, [])
            headers_by_table[table_name] = used_headers
            notes.append(
                f"Data/{csv_name}: header-only. '{table_name}' is a Hyper extract; "
                "row data loads only where tableauhyperapi is installed."
            )
            continue

        # 4) Demo seed for the synthetic calc fixture when it has no packaged CSV
        if not used_headers:
            used_headers = headers or ["Column1"]
            if used_headers == ["Column1"]:
                notes.append(f"Data/{csv_name}: no columns discovered — wrote stub header.")

        file_ref = _real_file_ref(ds)
        if file_ref:
            # A real file connection whose file we could not find: never invent rows.
            out[zip_path] = _rows_to_csv_bytes(used_headers, [])
            headers_by_table[table_name] = used_headers
            notes.append(
                f"Data/{csv_name}: header-only. Source file '{file_ref}' was not found next to "
                f"the workbook; repoint the '{table_name}' Power Query source to it."
            )
            continue

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
