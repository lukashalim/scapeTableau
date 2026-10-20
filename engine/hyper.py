"""Optional local Hyper → rows via official tableauhyperapi (not on Vercel)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_ROWS = 100_000
SKIP_SCHEMAS = {"pg_catalog", "information_schema"}


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
    # Hyper Date / Timestamp / Interval → portable text for Excel
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
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


def sheet_title_for(path: str, schema: str, table: str) -> str:
    stem = Path(path).stem or "hyper"
    return f"{stem}_{schema}_{table}"
