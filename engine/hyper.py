"""Optional local Hyper → rows via official tableauhyperapi (not on Vercel)."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_ROWS = 100_000
SKIP_SCHEMAS = {"pg_catalog", "information_schema"}
_TABLEAU_TEMP = re.compile(r"^#?TableauTemp_", re.IGNORECASE)
_BRACKET_PARTS = re.compile(r"\[([^\]]+)\]")


@dataclass
class HyperTableRows:
    schema: str
    table: str
    columns: list[str]
    rows: list[list[object]]
    truncated: bool = False


@dataclass
class HyperReadResult:
    tables: list[HyperTableRows]
    error: str | None = None


def hyper_conversion_enabled() -> bool:
    """Local CLI/Streamlit only. Never convert when VERCEL is set."""

    if os.environ.get("VERCEL"):
        return False
    try:
        import tableauhyperapi  # noqa: F401
    except Exception:
        return False
    return True


def _cell_value(value: object) -> object:
    if value is None:
        return ""
    # tableauhyperapi Date/Timestamp are not openpyxl-safe; convert first.
    # Timestamp has both to_datetime and to_date — prefer datetime.
    to_datetime = getattr(value, "to_datetime", None)
    if callable(to_datetime):
        try:
            return to_datetime()
        except Exception:
            pass
    to_date = getattr(value, "to_date", None)
    if callable(to_date):
        try:
            return to_date()
        except Exception:
            pass
    # Interval / other Hyper types → portable text
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    # Named Hyper values (Date without converters) still stringify cleanly
    module = getattr(type(value), "__module__", "") or ""
    if module.startswith("tableauhyperapi"):
        return str(value)
    return value


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _bare_name(value: object) -> str:
    text = str(value)
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1].replace('""', '"')
    return text


def _list_user_tables(connection) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for schema in connection.catalog.get_schema_names():
        schema_name = _bare_name(getattr(schema, "name", schema))
        if schema_name.lower() in SKIP_SCHEMAS:
            continue
        for table in connection.catalog.get_table_names(schema):
            table_name = _bare_name(getattr(table, "name", table))
            found.append((schema_name, table_name))
    return found


def read_hyper_bytes(data: bytes, *, max_rows: int = MAX_ROWS) -> HyperReadResult:
    """Read all user schemas/tables from Hyper bytes via a temp file for hyperd."""

    if not hyper_conversion_enabled():
        reason = (
            "Hyper conversion skipped on Vercel (hyperd / tableauhyperapi not used in serverless)."
            if os.environ.get("VERCEL")
            else "tableauhyperapi is not installed (optional: pip install tableauhyperapi)."
        )
        return HyperReadResult(tables=[], error=reason)

    from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry

    path: str | None = None
    try:
        fd, path = tempfile.mkstemp(suffix=".hyper")
        os.write(fd, data)
        os.close(fd)
        tables: list[HyperTableRows] = []
        with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
            with Connection(
                endpoint=hyper.endpoint,
                database=Path(path),
                create_mode=CreateMode.NONE,
            ) as connection:
                catalog = _list_user_tables(connection)
                if not catalog:
                    return HyperReadResult(tables=[], error="No user tables found in Hyper extract.")
                for schema, table in catalog:
                    sql = f"SELECT * FROM {_quote_ident(schema)}.{_quote_ident(table)}"
                    raw = connection.execute_list_query(sql)
                    truncated = len(raw) > max_rows
                    body = raw[:max_rows]
                    # Column names from table definition when available
                    columns: list[str] = []
                    try:
                        from tableauhyperapi import TableName

                        definition = connection.catalog.get_table_definition(
                            TableName(schema, table)
                        )
                        columns = [_bare_name(col.name) for col in definition.columns]
                    except Exception:
                        if body:
                            columns = [f"c{i}" for i in range(len(body[0]))]
                    rows = [[_cell_value(cell) for cell in row] for row in body]
                    tables.append(
                        HyperTableRows(
                            schema=schema,
                            table=table,
                            columns=columns,
                            rows=rows,
                            truncated=truncated,
                        )
                    )
        return HyperReadResult(tables=tables)
    except Exception as exc:  # noqa: BLE001 — corrupt hyper / hyperd failures
        return HyperReadResult(tables=[], error=str(exc))
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def is_tableau_temp_table(name: str | None) -> bool:
    """True for Tableau extract temp table ids (#TableauTemp_… / TableauTemp_…)."""

    if not name:
        return False
    return bool(_TABLEAU_TEMP.match(name.strip().strip("[]")))


def relation_table_parts(table_attr: str | None) -> tuple[str | None, str | None]:
    """Parse a TWB relation table= attribute into (schema, table)."""

    if not table_attr:
        return None, None
    parts = _BRACKET_PARTS.findall(table_attr)
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return None, parts[0]
    bare = table_attr.strip().strip("[]")
    if "." in bare:
        schema, _, table = bare.partition(".")
        return schema or None, table or None
    return None, bare or None


def _usable_table_label(table: str | None) -> str | None:
    if not table:
        return None
    cleaned = table.strip().strip("[]")
    if not cleaned or is_tableau_temp_table(cleaned):
        return None
    if cleaned.lower() == "extract":
        return None
    return cleaned


def sheet_title_for(
    path: str,
    schema: str,
    table: str,
    *,
    preferred: str | None = None,
) -> str:
    """Excel sheet base name for a Hyper table (caller applies 31-char uniqueness).

    Prefer a TWB relation/caption when provided. Never use TableauTemp_* ids.
    Fallback: zip member stem, then \"Extract\".
    """

    if preferred and preferred.strip():
        return preferred.strip()
    cleaned = _usable_table_label(table)
    if cleaned:
        return cleaned
    stem = (Path(path).stem or "").strip()
    if stem and stem.lower() not in {"extract", "data", "hyper"}:
        return stem
    return "Extract"


def preferred_sheet_names_for_hyper(
    datasources: list[object],
    hyper_path: str,
    tables: list[HyperTableRows],
) -> list[str | None]:
    """One preferred sheet title per Hyper table (None = use sheet_title_for fallback).

    Uses TWB datasource caption / relation name when the datasource points at this
    Hyper file, or when relation.table matches the Hyper table id.
    """

    from engine.schema import DatasourceInfo

    path_norm = hyper_path.replace("\\", "/").lower()
    path_base = Path(hyper_path).name.lower()
    by_table: dict[str, str] = {}
    ds_labels: list[str] = []

    for raw in datasources:
        if not isinstance(raw, DatasourceInfo):
            continue
        ds = raw
        if (ds.name or "").lower() == "parameters":
            continue

        connected = False
        for conn in ds.connections:
            class_name = (conn.class_name or "").lower()
            refs = " ".join(
                p for p in (conn.dbname, conn.filename) if p
            ).replace("\\", "/").lower()
            if (
                "hyper" in class_name
                or path_base in refs
                or path_norm in refs
                or refs.endswith(".hyper")
                or "/extract" in refs
            ):
                connected = True
                break

        for rel in ds.relations:
            _schema, rel_table = relation_table_parts(rel.table)
            label = (rel.name or "").strip() or None
            if not label and connected:
                label = (ds.caption or "").strip() or None
            if not label:
                continue
            if rel_table:
                key = rel_table.strip().strip("[]")
                by_table[key] = label
                by_table[key.lstrip("#")] = label
            if connected and label not in ds_labels:
                ds_labels.append(label)

        if connected and not any(rel.name for rel in ds.relations):
            cap = (ds.caption or "").strip()
            if cap and cap not in ds_labels:
                ds_labels.append(cap)

    preferred: list[str | None] = []
    for table in tables:
        key = (table.table or "").strip().strip("[]")
        label = by_table.get(key) or by_table.get(key.lstrip("#"))
        preferred.append(label)

    # Single extract table + one connected label → use it even without table= match
    if (
        len(tables) == 1
        and preferred
        and preferred[0] is None
        and len(ds_labels) == 1
    ):
        preferred[0] = ds_labels[0]

    return preferred
