"""Power Query M partition expressions for Tableau datasources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from engine.schema import ConnectionInfo, DatasourceInfo, RelationInfo


@dataclass
class MPartitionPlan:
    source_kind: str  # csv | postgres | sqlserver | custom_sql | hyper | unknown
    expression: str
    parameters: list[tuple[str, str, str]]  # (name, kind, expression)
    notes: list[str]


def _safe_param(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", (name or "Source").strip())
    if not cleaned or cleaned[0].isdigit():
        cleaned = "P_" + cleaned
    return cleaned


def _primary_connection(ds: DatasourceInfo) -> ConnectionInfo | None:
    for conn in ds.connections:
        cls = (conn.class_name or "").lower()
        if cls and cls != "federated":
            return conn
    return ds.connections[0] if ds.connections else None


def _primary_relation(ds: DatasourceInfo) -> RelationInfo | None:
    for rel in ds.relations:
        if rel.custom_sql or rel.table or rel.name:
            return rel
    return None


def _type_transforms(columns: list[tuple[str, str]]) -> str:
    """Build Table.TransformColumnTypes list from (name, m_type) pairs."""

    if not columns:
        return "null"
    parts = []
    for name, mtype in columns:
        escaped = name.replace('"', '""')
        parts.append(f'{{"{escaped}", {mtype}}}')
    return "{" + ", ".join(parts) + "}"


def tableau_type_to_m(datatype: str | None) -> str:
    dt = (datatype or "string").lower()
    if dt in {"integer", "int"}:
        return "Int64.Type"
    if dt in {"real", "float", "double", "number"}:
        return "type number"
    if dt in {"date"}:
        return "type date"
    if dt in {"datetime", "timestamp"}:
        return "type datetime"
    if dt in {"boolean", "bool"}:
        return "type logical"
    return "type text"


# Small extracts are embedded in the M partition. A relative DataFolder path does
# not resolve when Desktop opens a downloaded .pbip, so File.Contents("Data\\…")
# leaves every visual empty until someone sets an absolute path and refreshes.
INLINE_CSV_MAX_CHARS = 200_000


def m_text_literal(text: str) -> str:
    """M text literal. ``#`` and quotes are escaped; newlines become ``#(lf)``."""

    escaped = text.replace("#", "#(hash)").replace('"', '""')
    escaped = (
        escaped.replace("\r\n", "#(cr)#(lf)")
        .replace("\n", "#(lf)")
        .replace("\r", "#(cr)")
    )
    return f'"{escaped}"'


def _typed_csv_query(source_expr: str, transforms: str) -> str:
    """Promote headers, turn blank cells into null, then apply column types.

    A blank Discount/Quantity cell typed as a number is an error and can leave
    the whole table empty, so every measure on the visual stays blank.
    """

    return f"""let
    Source = {source_expr},
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Replaced Blanks" = Table.ReplaceValue(#"Promoted Headers", "", null, Replacer.ReplaceValue, Table.ColumnNames(#"Promoted Headers")),
    Typed = if {transforms} = null then #"Replaced Blanks" else Table.TransformColumnTypes(#"Replaced Blanks", {transforms})
in
    Typed"""


def _csv_inline(csv_text: str, transforms: str) -> str:
    """M that loads CSV text embedded in the partition (no file path, no credentials)."""

    literal = m_text_literal(csv_text)
    source = (
        f"Csv.Document(Text.ToBinary({literal}, 65001), "
        f'[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])'
    )
    return _typed_csv_query(source, transforms)


def _csv_from_datafolder(filename: str, transforms: str) -> str:
    """M that loads a CSV under the shared DataFolder parameter."""

    # Escape for M string literal inside TMDL (backslash + quotes)
    escaped = (filename or "table.csv").replace('"', '""').replace("\\", "\\\\")
    source = (
        f'Csv.Document(File.Contents(DataFolder & "\\{escaped}"), '
        f'[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.None])'
    )
    return _typed_csv_query(source, transforms)


def _bind_csv_expression(
    filename: str,
    transforms: str,
    csv_text: str | None,
    *,
    inline_max_chars: int,
) -> tuple[str, bool]:
    """Return ``(m_expression, embedded)``. Embed when the CSV fits the cap."""

    text = (csv_text or "").strip("\ufeff")
    if text and len(text) <= inline_max_chars:
        return _csv_inline(text, transforms), True
    return _csv_from_datafolder(filename, transforms), False


def _csv_plan(
    *,
    table_name: str,
    csv_name: str,
    transforms: str,
    kind: str,
    note: str,
    csv_text: str | None = None,
    inline_max_chars: int = INLINE_CSV_MAX_CHARS,
) -> MPartitionPlan:
    filename = Path(csv_name).name
    expression, embedded = _bind_csv_expression(
        filename,
        transforms,
        csv_text,
        inline_max_chars=inline_max_chars,
    )
    if embedded:
        rows = max(0, len([ln for ln in (csv_text or "").splitlines() if ln.strip()]) - 1)
        notes = [
            f"Embedded {rows} data rows for '{table_name}' in the M partition "
            "(no Postgres login and no DataFolder path). "
            "In Desktop, use Refresh now / Apply changes once so the visual loads."
        ]
    else:
        notes = [note]
    return MPartitionPlan(
        kind,
        expression,
        [],
        notes,
    )


def plan_m_partition(
    ds: DatasourceInfo,
    *,
    table_name: str,
    column_types: list[tuple[str, str]],
    csv_filename: str | None = None,
    prefer_csv: bool = True,
    csv_text: str | None = None,
    inline_max_chars: int = INLINE_CSV_MAX_CHARS,
) -> MPartitionPlan:
    """Plan an M partition for a Tableau datasource.

    ``prefer_csv=True`` (default) keeps file, Hyper, Custom SQL, and live
    Postgres/SQL Server tables off live connectors. Small CSVs are embedded in
    the M partition so Desktop shows rows after Apply changes. Larger CSVs use
    ``DataFolder\\\\<table>.csv``. Pass ``prefer_csv=False`` to emit live DB
    connectors instead.
    """

    conn = _primary_connection(ds)
    rel = _primary_relation(ds)
    notes: list[str] = []
    params: list[tuple[str, str, str]] = []
    cls = ((conn.class_name if conn else None) or "").lower()
    transforms = _type_transforms(column_types)
    csv_name = csv_filename or f"{table_name}.csv"

    # Desktop-open path: always bind to the Data/ CSV we emit beside the .pbip.
    if prefer_csv and (
        (rel and rel.custom_sql)
        or cls in {"postgres", "postgresql", "sqlserver", "sqlproxy", "hyper"}
        or (
            cls in {"textscan", "text", "csv", "excel-direct"}
            or (conn and conn.filename and str(conn.filename).lower().endswith((".csv", ".txt", ".tsv")))
        )
        or (conn and conn.dbname and str(conn.dbname).lower().endswith(".hyper"))
        or column_types
    ):
        filename = Path(csv_name).name
        if rel and rel.custom_sql:
            note = (
                f"Custom SQL for '{table_name}' materialized to DataFolder\\\\{filename} "
                "(live DB skipped so Desktop opens without credentials)."
            )
            kind = "csv"
        elif cls in {"postgres", "postgresql", "sqlserver", "sqlproxy"}:
            note = (
                f"Live {cls} table '{table_name}' → DataFolder\\\\{filename} "
                "(offline CSV for Desktop open)."
            )
            kind = "csv"
        elif cls == "hyper" or (conn and conn.dbname and str(conn.dbname).lower().endswith(".hyper")):
            hyper_path = (conn.dbname if conn else None) or (conn.filename if conn else None) or "extract.hyper"
            note = (
                f"Hyper extract '{hyper_path}' → Data/{filename} via DataFolder "
                "(rows when Hyper conversion is available; otherwise header-only)."
            )
            kind = "hyper"
        elif cls in {"textscan", "text", "csv", "excel-direct"} or (
            conn and conn.filename and str(conn.filename).lower().endswith((".csv", ".txt", ".tsv"))
        ):
            note = f"CSV partition for '{table_name}' → DataFolder\\\\{filename}"
            kind = "csv"
        else:
            note = (
                f"Unknown connection class '{cls or 'none'}' for '{table_name}' — "
                f"using DataFolder\\\\{filename} (header stub if no extract)."
            )
            kind = "unknown"
        return _csv_plan(
            table_name=table_name,
            csv_name=csv_name,
            transforms=transforms,
            kind=kind,
            note=note,
            csv_text=csv_text,
            inline_max_chars=inline_max_chars,
        )

    if rel and rel.custom_sql:
        server = (conn.server if conn else None) or "localhost"
        dbname = (conn.dbname if conn else None) or "database"
        # Strip redaction markers for parameter scaffolding; passwords stay out.
        server_clean = server.split(";")[0]
        p_server = _safe_param(f"{table_name}_Server")
        p_db = _safe_param(f"{table_name}_Database")
        params.append((p_server, "text", f'"{server_clean}" meta [IsParameterQuery=true, Type="Text"]'))
        params.append((p_db, "text", f'"{dbname}" meta [IsParameterQuery=true, Type="Text"]'))
        sql = (rel.custom_sql or "").replace('"', '""')
        if cls in {"sqlserver", "sqlproxy"}:
            connector = (
                f'Sql.Database({p_server}, {p_db}, [Query="{sql}"])'
            )
            kind = "sqlserver"
        else:
            connector = (
                f'PostgreSQL.Database({p_server}, {p_db}, [Query="{sql}"])'
                if cls in {"postgres", "postgresql"}
                else f'Value.NativeQuery(PostgreSQL.Database({p_server}, {p_db}), "{sql}", null, [EnableFolding=true])'
            )
            kind = "custom_sql" if cls not in {"postgres", "postgresql"} else "postgres"
        expr = f"""let
    Source = {connector},
    Typed = if {transforms} = null then Source else Table.TransformColumnTypes(Source, {transforms})
in
    Typed"""
        notes.append(f"Custom SQL / live query for '{table_name}' — bind credentials in Desktop.")
        return MPartitionPlan(kind, expr, params, notes)

    if cls in {"textscan", "text", "csv", "excel-direct"} or (
        conn and conn.filename and str(conn.filename).lower().endswith((".csv", ".txt", ".tsv"))
    ):
        filename = Path(csv_name).name
        notes.append(f"CSV partition for '{table_name}' → DataFolder\\\\{filename}")
        return MPartitionPlan("csv", _csv_from_datafolder(filename, transforms), params, notes)

    if cls in {"postgres", "postgresql"}:
        server = ((conn.server if conn else None) or "localhost").split(";")[0]
        dbname = (conn.dbname if conn else None) or "database"
        schema_table = (rel.table if rel else None) or f"[public].[{table_name}]"
        # Tableau [schema].[table] → schema/table
        parts = re.findall(r"\[([^\]]+)\]", schema_table) or [table_name]
        schema = parts[0] if len(parts) > 1 else "public"
        table = parts[-1]
        p_server = _safe_param(f"{table_name}_Server")
        p_db = _safe_param(f"{table_name}_Database")
        params.append((p_server, "text", f'"{server}" meta [IsParameterQuery=true, Type="Text"]'))
        params.append((p_db, "text", f'"{dbname}" meta [IsParameterQuery=true, Type="Text"]'))
        expr = f"""let
    Source = PostgreSQL.Database({p_server}, {p_db}),
    Nav = Source{{[Schema="{schema}", Item="{table}"]}}[Data],
    Typed = if {transforms} = null then Nav else Table.TransformColumnTypes(Nav, {transforms})
in
    Typed"""
        return MPartitionPlan("postgres", expr, params, ["Postgres live connection — set credentials in Desktop."])

    if cls in {"sqlserver", "sqlproxy"}:
        server = ((conn.server if conn else None) or "localhost").split(";")[0]
        dbname = (conn.dbname if conn else None) or "database"
        parts = re.findall(r"\[([^\]]+)\]", (rel.table if rel else None) or table_name) or [table_name]
        schema = parts[0] if len(parts) > 1 else "dbo"
        table = parts[-1]
        p_server = _safe_param(f"{table_name}_Server")
        p_db = _safe_param(f"{table_name}_Database")
        params.append((p_server, "text", f'"{server}" meta [IsParameterQuery=true, Type="Text"]'))
        params.append((p_db, "text", f'"{dbname}" meta [IsParameterQuery=true, Type="Text"]'))
        expr = f"""let
    Source = Sql.Database({p_server}, {p_db}),
    Nav = Source{{[Schema="{schema}", Item="{table}"]}}[Data],
    Typed = if {transforms} = null then Nav else Table.TransformColumnTypes(Nav, {transforms})
in
    Typed"""
        return MPartitionPlan("sqlserver", expr, params, ["SQL Server live connection — set credentials in Desktop."])

    if cls == "hyper" or (conn and conn.dbname and str(conn.dbname).lower().endswith(".hyper")):
        hyper_path = (conn.dbname if conn else None) or (conn.filename if conn else None) or "extract.hyper"
        filename = Path(csv_name).name
        notes.append(
            f"Hyper extract '{hyper_path}' → Data/{filename} via DataFolder "
            "(rows when Hyper conversion is available; otherwise header-only)."
        )
        return MPartitionPlan(
            "hyper", _csv_from_datafolder(filename, transforms), params, notes
        )

    # File-ish / unknown: still prefer DataFolder CSV when we emit Data/<table>.csv
    if column_types:
        filename = Path(csv_name).name
        notes.append(
            f"Unknown connection class '{cls or 'none'}' for '{table_name}' — "
            f"using DataFolder\\\\{filename} (header stub if no extract)."
        )
        return MPartitionPlan(
            "unknown", _csv_from_datafolder(filename, transforms), params, notes
        )

    notes.append(f"Unknown connection class '{cls or 'none'}' for '{table_name}' — empty typed stub.")
    col_names = ", ".join(f'"{n}"' for n, _ in column_types) or '"Column1"'
    expr = f"""let
    Source = #table({{{col_names}}}, {{}})
in
    Source"""
    return MPartitionPlan("unknown", expr, params, notes)
