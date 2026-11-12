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

    # M's escape for a literal "#" is "#(#)"; anything else after "#(" is an invalid literal.
    escaped = text.replace("#", "#(#)").replace('"', '""')
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

    if transforms == "null":
        return f"""let
    Source = {source_expr},
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Replaced Blanks" = Table.ReplaceValue(#"Promoted Headers", "", null, Replacer.ReplaceValue, Table.ColumnNames(#"Promoted Headers"))
in
    #"Replaced Blanks\""""

    # Tableau extracts can omit columns that exist in the model (e.g. stub tables).
    # TransformColumnTypes fails with "column ... wasn't found", so pad them with null first.
    # MissingField.UseNull creates absent columns; no lambdas needed.
    names = re.findall(r'\{("(?:[^"]|"")*"),', transforms)
    ops = ", ".join(f"{{{n}, each _}}" for n in names)
    return f"""let
    Source = {source_expr},
    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"Replaced Blanks" = Table.ReplaceValue(#"Promoted Headers", "", null, Replacer.ReplaceValue, Table.ColumnNames(#"Promoted Headers")),
    Ensured = Table.TransformColumns(#"Replaced Blanks", {{{ops}}}, null, MissingField.UseNull),
    Typed = Table.TransformColumnTypes(Ensured, {transforms})
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
        # Data/*.csv is written with standard quoting; QuoteStyle.None would split
        # quoted fields that contain commas (cast lists, descriptions).
        f'[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])'
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
        if rows == 0:
            notes = [
                f"Embedded 0 data rows for '{table_name}' (no extract in the workbook). "
                "The model and visuals are built, but they stay empty until this table is loaded."
            ]
        else:
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


_LIVE_DB_CLASSES = {"postgres", "postgresql", "sqlserver"}
_EXTRACT_CLASSES = {"hyper", "sqlproxy"}
_FILE_CLASSES = {"textscan", "text", "csv", "excel-direct"}
_SSL_REQUIRED = {"require", "verify-ca", "verify-full", "true", "yes"}


def _class_name(conn: ConnectionInfo | None) -> str:
    return ((conn.class_name if conn else None) or "").lower()


def _is_extract_connection(conn: ConnectionInfo) -> bool:
    if _class_name(conn) in _EXTRACT_CLASSES:
        return True
    name = (conn.dbname or conn.filename or "").lower()
    return name.endswith(".hyper")


def _is_file_connection(conn: ConnectionInfo | None) -> bool:
    if conn is None:
        return False
    if _class_name(conn) in _FILE_CLASSES:
        return True
    filename = (conn.filename or "").lower()
    return filename.endswith((".csv", ".txt", ".tsv", ".xlsx", ".xls"))


def keeps_live_database(ds: DatasourceInfo) -> bool:
    """True when the partition should be a database connector, not a CSV."""

    return _live_db_connection(ds) is not None


def _live_db_connection(ds: DatasourceInfo) -> ConnectionInfo | None:
    """Database connection to preserve when the workbook is not using an extract."""

    if ds.extract_enabled:
        return None
    live = [c for c in ds.connections if _class_name(c) in _LIVE_DB_CLASSES]
    return live[0] if live else None


def _server_host(conn: ConnectionInfo | None) -> str:
    """Hostname only. Connection strings can carry ``;PWD=`` and that stays out."""

    raw = ((conn.server if conn else None) or "localhost").strip()
    host = raw.split(";")[0].strip()
    host = re.sub(r"(?i)^tcp:", "", host)
    return host or "localhost"


def _server_endpoint(conn: ConnectionInfo | None) -> str:
    host = _server_host(conn)
    port = ((conn.port if conn else None) or "").strip()
    if port and f":{port}" not in host:
        return f"{host}:{port}"
    return host


def _database_name(conn: ConnectionInfo | None) -> str:
    raw = ((conn.dbname if conn else None) or "database").strip()
    name = raw.split(";")[0].strip()
    if not name or name.lower().endswith(".hyper"):
        return "database"
    return name


def _credential_note(conn: ConnectionInfo, host: str, dbname: str, table_name: str, engine: str) -> str:
    user = (conn.username or "").strip()
    sign_in = f" Sign in as {user}." if user else " Sign in when Desktop asks."
    ssl = (conn.sslmode or "").strip().lower()
    encrypt = " Turn on Encrypt connection." if ssl in _SSL_REQUIRED else ""
    return (
        f"{engine} connection for '{table_name}' stays live ({host} / {dbname})."
        f"{sign_in}{encrypt} The password is not stored in the project."
    )


def _param_expr(value: str) -> str:
    return f'{m_text_literal(value)} meta [IsParameterQuery=true, Type="Text"]'


def _schema_table(raw: str | None, default_schema: str, table_name: str) -> tuple[str, str]:
    parts = re.findall(r"\[([^\]]+)\]", raw or "") or [table_name]
    schema = parts[0] if len(parts) > 1 else default_schema
    table = parts[-1]
    return schema.replace('"', '""'), table.replace('"', '""')


def _local_column_name(raw: str | None) -> str:
    text = (raw or "").strip()
    if "].[" in text:
        text = text.split("].[")[-1]
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return text


def _caption_renames(ds: DatasourceInfo) -> list[tuple[str, str]]:
    """Query names that differ from the Tableau caption.

    Postgres folds an unquoted ``state`` to lowercase. The model column is the
    caption (``State``). ``Table.TransformColumnTypes`` is case-sensitive, so
    the load fails with "column 'State' wasn't found" unless the result is
    renamed first.
    """

    renames: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field in ds.fields:
        if field.calculated or (field.datatype or "").lower() == "table":
            continue
        if "__tableau_internal_object_id__" in (field.name or ""):
            continue
        caption = (field.caption or field.name or "").strip("[]")
        local = _local_column_name(field.name)
        if not caption or not local:
            continue
        for source in (local, local.lower()):
            pair = (source, caption)
            if source != caption and pair not in seen:
                seen.add(pair)
                renames.append(pair)
    return renames


def _rename_list(renames: list[tuple[str, str]]) -> str:
    parts = []
    for src, dst in renames:
        parts.append(
            f'{{"{src.replace(chr(34), chr(34) * 2)}", "{dst.replace(chr(34), chr(34) * 2)}"}}'
        )
    return "{" + ", ".join(parts) + "}"


def _typed_live_query(connector: str, transforms: str, renames: list[tuple[str, str]]) -> str:
    rename_expr = _rename_list(renames)
    if rename_expr == "{}":
        return f"""let
    Source = {connector},
    Typed = if {transforms} = null then Source else Table.TransformColumnTypes(Source, {transforms})
in
    Typed"""
    return f"""let
    Source = {connector},
    Renamed = Table.RenameColumns(Source, {rename_expr}, MissingField.Ignore),
    Typed = if {transforms} = null then Renamed else Table.TransformColumnTypes(Renamed, {transforms})
in
    Typed"""


def _live_db_plan(
    conn: ConnectionInfo,
    rel: RelationInfo | None,
    *,
    table_name: str,
    transforms: str,
    renames: list[tuple[str, str]] | None = None,
) -> MPartitionPlan:
    """Power Query connector for a live Postgres or SQL Server source.

    Credentials stay in Desktop's prompt. The password attribute and any
    ``PWD=`` fragment on the server string are never written into the project.
    """

    cls = _class_name(conn)
    host = _server_endpoint(conn)
    dbname = _database_name(conn)
    p_server = _safe_param(f"{table_name}_Server")
    p_db = _safe_param(f"{table_name}_Database")
    params = [
        (p_server, "text", _param_expr(host)),
        (p_db, "text", _param_expr(dbname)),
    ]
    engine = "SQL Server" if cls == "sqlserver" else "Postgres"
    renames = renames or []
    if rel and rel.custom_sql:
        sql_lit = m_text_literal(rel.custom_sql)
        if cls == "sqlserver":
            connector = f"Sql.Database({p_server}, {p_db}, [Query={sql_lit}])"
            kind = "sqlserver"
        else:
            connector = f"PostgreSQL.Database({p_server}, {p_db}, [Query={sql_lit}])"
            kind = "postgres"
        return MPartitionPlan(
            kind,
            _typed_live_query(connector, transforms, renames),
            params,
            [_credential_note(conn, host, dbname, table_name, f"{engine} custom SQL")],
        )

    default_schema = "dbo" if cls == "sqlserver" else "public"
    schema, table = _schema_table(rel.table if rel else None, default_schema, table_name)
    rename_expr = _rename_list(renames)
    renamed = (
        f"""    Renamed = Table.RenameColumns(Nav, {rename_expr}, MissingField.Ignore),
"""
        if rename_expr != "{}"
        else ""
    )
    typed_from = "Renamed" if renamed else "Nav"
    if cls == "sqlserver":
        expr = f"""let
    Source = Sql.Database({p_server}, {p_db}),
    Nav = Source{{[Schema="{schema}", Item="{table}"]}}[Data],
{renamed}    Typed = if {transforms} = null then {typed_from} else Table.TransformColumnTypes({typed_from}, {transforms})
in
    Typed"""
        kind = "sqlserver"
    else:
        expr = f"""let
    Source = PostgreSQL.Database({p_server}, {p_db}),
    Nav = Source{{[Schema="{schema}", Item="{table}"]}}[Data],
{renamed}    Typed = if {transforms} = null then {typed_from} else Table.TransformColumnTypes({typed_from}, {transforms})
in
    Typed"""
        kind = "postgres"
    return MPartitionPlan(
        kind,
        expr,
        params,
        [_credential_note(conn, host, dbname, table_name, engine)],
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
    """Plan an M partition that follows the Tableau source.

    A live Postgres or SQL Server connection stays a live connector. Desktop
    asks for the password on refresh; it is not copied into the project.
    An enabled extract, a Hyper or Tableau Server extract (``sqlproxy``), or a
    file stays a CSV. Small CSVs are embedded in the M partition. Larger ones
    use ``DataFolder\\\\<table>.csv``. ``prefer_csv`` does not turn a live
    database into a file.
    """

    conn = _primary_connection(ds)
    rel = _primary_relation(ds)
    cls = _class_name(conn)
    transforms = _type_transforms(column_types)
    csv_name = csv_filename or f"{table_name}.csv"

    live = _live_db_connection(ds)
    if live is not None:
        return _live_db_plan(
            live,
            rel,
            table_name=table_name,
            transforms=transforms,
            renames=_caption_renames(ds),
        )

    filename = Path(csv_name).name
    extract_conns = [c for c in ds.connections if _is_extract_connection(c)]
    if ds.extract_enabled or extract_conns:
        hyper_path = next(
            (
                (c.dbname or c.filename)
                for c in extract_conns
                if c.dbname or c.filename
            ),
            "extract",
        )
        note = (
            f"Extract '{hyper_path}' for '{table_name}' stays a CSV at Data/{filename} "
            "(rows when the extract can be read; otherwise header-only)."
        )
        kind = "hyper"
    elif _is_file_connection(conn):
        note = f"CSV partition for '{table_name}' → DataFolder\\\\{filename}"
        kind = "csv"
    elif rel and rel.custom_sql:
        note = (
            f"Custom SQL for '{table_name}' materialized to DataFolder\\\\{filename} "
            "(no live database connection on this datasource)."
        )
        kind = "csv"
    elif prefer_csv or column_types:
        note = (
            f"Unknown connection class '{cls or 'none'}' for '{table_name}' — "
            f"using DataFolder\\\\{filename} (header stub if no extract)."
        )
        kind = "unknown"
    else:
        notes = [f"Unknown connection class '{cls or 'none'}' for '{table_name}' — empty typed stub."]
        col_names = ", ".join(f'"{n}"' for n, _ in column_types) or '"Column1"'
        expr = f"""let
    Source = #table({{{col_names}}}, {{}})
in
    Source"""
        return MPartitionPlan("unknown", expr, [], notes)

    return _csv_plan(
        table_name=table_name,
        csv_name=csv_name,
        transforms=transforms,
        kind=kind,
        note=note,
        csv_text=csv_text,
        inline_max_chars=inline_max_chars,
    )
