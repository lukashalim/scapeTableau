"""Assemble sql_script and markdown_audit from transpile outputs."""

from __future__ import annotations

import re

from engine.schema import (
    CalculatedField,
    DashboardInfo,
    DatasourceInfo,
    ExtractInventory,
    InspectSnapshot,
    ParameterInfo,
    ParseStatus,
    TranspileResult,
)


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _view_name(field: CalculatedField) -> str:
    base = field.caption or field.name
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", " "} else "_" for ch in base).strip()
    return cleaned or "calc"


def _primary_relation(ds: DatasourceInfo) -> str:
    from engine.transpiler import normalize_table_name

    for rel in ds.relations:
        table = normalize_table_name(rel.table) or normalize_table_name(rel.name)
        if table:
            return _sql_ident(table)
    if ds.caption:
        return _sql_ident(ds.caption)
    return _sql_ident(ds.name)


def _datasource_comment(ds: DatasourceInfo) -> list[str]:
    lines = [f"-- Datasource: {ds.caption or ds.name} ({ds.name})"]
    for conn in ds.connections:
        bits = [part for part in (conn.class_name, conn.server, conn.dbname, conn.filename) if part]
        if bits:
            lines.append("--   connection: " + " | ".join(bits))
    for rel in ds.relations:
        if rel.type == "text" and rel.custom_sql:
            preview = " ".join(rel.custom_sql.split())
            if len(preview) > 180:
                preview = preview[:177] + "..."
            lines.append(f"--   custom SQL ({rel.name or 'relation'}): {preview}")
        elif rel.table or rel.name:
            lines.append(f"--   relation: {rel.type or 'table'} {rel.table or rel.name}")
    return lines


def build_sql_script(
    fields: list[CalculatedField],
    datasources: list[DatasourceInfo],
    *,
    workbook_name: str | None = None,
    dialect: str = "ansi",
    script_style: str = "sql",
) -> str:
    lines: list[str] = [
        "-- EscapeTableau generated SQL",
        "-- Dialect: " + dialect,
    ]
    if workbook_name:
        lines.append(f"-- Source workbook: {workbook_name}")
    lines.append("-- Files were parsed in memory; review LOD / unmapped functions before running.")
    lines.append("")
    for ds in datasources:
        lines.extend(_datasource_comment(ds))
        lines.append("")

    ds_by_caption = {ds.caption: ds for ds in datasources if ds.caption}
    ds_by_name = {ds.name: ds for ds in datasources}
    for field in fields:
        if field.status != ParseStatus.ok:
            lines.append(f"-- SKIPPED [{field.status.value}] {field.caption}: {field.error or 'see SQL fallback'}")
            for sql_line in field.sql.splitlines() or [field.sql]:
                if sql_line.startswith("--"):
                    lines.append(sql_line)
                else:
                    lines.append(f"-- {sql_line}")
            lines.append("")
            continue
        view = _sql_ident(_view_name(field))
        ds = ds_by_name.get(field.datasource) or ds_by_caption.get(field.datasource)
        from_rel = _primary_relation(ds) if ds else _sql_ident("Orders")
        if script_style == "dbt":
            lines.append("{{ config(materialized='view') }}")
        lines.append(f"CREATE VIEW {view} AS")
        lines.append("SELECT")
        lines.append(f"  {field.sql} AS {view}")
        lines.append(f"FROM {from_rel};")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _dax_table_name(fields: list[CalculatedField]) -> str:
    ident = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[")
    for field in fields:
        match = ident.search(field.dax or "")
        if match:
            return match.group(1)
    return "Orders"


def build_dax_script(
    fields: list[CalculatedField],
    *,
    workbook_name: str | None = None,
    parameters: list[ParameterInfo] | None = None,
) -> str:
    from engine.transpiler import param_dax_literal, param_measure_name

    lines = ["// BIEscape generated DAX"]
    if workbook_name:
        lines.append(f"// Source workbook: {workbook_name}")
    table = _dax_table_name(fields)
    measures = [f for f in fields if (f.dax_kind or "measure") == "measure"]
    columns = [f for f in fields if (f.dax_kind or "measure") != "measure"]

    lines.append("")
    lines.append("// Parameters")
    params = list(parameters or [])
    if not params:
        lines.append("// (none)")
    else:
        for info in params:
            lines.append(f"[{param_measure_name(info.caption)}] := {param_dax_literal(info)}")
        lines.append("")

    def _emit_section(title: str, group: list[CalculatedField], *, kind: str) -> None:
        lines.append("")
        lines.append(f"// {title}")
        if not group:
            lines.append("// (none)")
            return
        for field in group:
            dax_st = field.dax_status.value if field.dax_status is not None else "unknown"
            lines.append(f"// {field.caption} (dax_status={dax_st})")
            body = field.dax or "// (no DAX)"
            name = field.caption or field.name
            if kind == "measure":
                assign = f"[{name}] := {body}"
            else:
                assign = f"{table}[{name}] = {body}"
            for dax_line in assign.splitlines() or [assign]:
                lines.append(dax_line)
            lines.append("")

    _emit_section("Measures", measures, kind="measure")
    _emit_section("Calculated columns", columns, kind="column")
    return "\n".join(lines).rstrip() + "\n"


def _md_cell(value: str | None) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def build_markdown_audit(fields: list[CalculatedField], datasources: list[DatasourceInfo]) -> str:
    lines = [
        "# EscapeTableau audit",
        "",
        f"Calculated fields: **{len(fields)}**. Datasources: **{len(datasources)}**.",
        "",
        "| Caption | Datasource | Role | Datatype | Status | Tableau formula | SQL | DAX |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for field in fields:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(field.caption),
                    _md_cell(field.datasource),
                    _md_cell(field.role),
                    _md_cell(field.datatype),
                    _md_cell(field.status.value),
                    _md_cell(field.formula),
                    _md_cell(field.sql),
                    _md_cell(field.dax),
                ]
            )
            + " |"
        )
    ok = sum(1 for f in fields if f.status == ParseStatus.ok)
    unp = sum(1 for f in fields if f.status == ParseStatus.unparseable)
    err = sum(1 for f in fields if f.status == ParseStatus.error)
    lines.extend(
        [
            "",
            f"- ok: {ok}",
            f"- unparseable: {unp}",
            f"- error: {err}",
            "",
        ]
    )
    return "\n".join(lines)


def assemble_result(
    fields: list[CalculatedField],
    datasources: list[DatasourceInfo],
    *,
    workbook_name: str | None = None,
    dialect: str = "ansi",
    inspect: InspectSnapshot | None = None,
    extract_inventory: ExtractInventory | None = None,
    dashboards: list[DashboardInfo] | None = None,
    target: str = "sql",
    script_style: str = "sql",
    parameters: list[ParameterInfo] | None = None,
) -> TranspileResult:
    return TranspileResult(
        calculated_fields=fields,
        datasources=datasources,
        sql_script=build_sql_script(
            fields,
            datasources,
            workbook_name=workbook_name,
            dialect=dialect,
            script_style=script_style,
        ),
        markdown_audit=build_markdown_audit(fields, datasources),
        workbook_name=workbook_name,
        dialect=dialect,
        inspect=inspect,
        extract_inventory=extract_inventory,
        dashboards=list(dashboards or (inspect.dashboards if inspect else [])),
        dax_script=build_dax_script(
            fields, workbook_name=workbook_name, parameters=parameters
        ),
        target=target,
    )
