"""Assemble sql_script and markdown_audit from transpile outputs."""

from __future__ import annotations

from engine.schema import CalculatedField, DatasourceInfo, ParseStatus, TranspileResult


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _view_name(field: CalculatedField) -> str:
    base = field.caption or field.name
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", " "} else "_" for ch in base).strip()
    return cleaned or "calc"


def _primary_relation(ds: DatasourceInfo) -> str:
    for rel in ds.relations:
        if rel.table:
            table = rel.table.strip()
            if table.startswith("[") and table.endswith("]"):
                table = table[1:-1]
            return _sql_ident(table)
        if rel.name:
            return _sql_ident(rel.name)
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
        ds = ds_by_name.get(field.datasource)
        from_rel = _primary_relation(ds) if ds else _sql_ident("source")
        lines.append("{{ config(materialized='view') }}")
        lines.append(f"CREATE VIEW {view} AS")
        lines.append("SELECT")
        lines.append(f"  {field.sql} AS {view}")
        lines.append(f"FROM {from_rel};")
        lines.append("")
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
        "| Caption | Datasource | Role | Datatype | Status | Tableau formula | SQL |",
        "| --- | --- | --- | --- | --- | --- | --- |",
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
) -> TranspileResult:
    return TranspileResult(
        calculated_fields=fields,
        datasources=datasources,
        sql_script=build_sql_script(
            fields, datasources, workbook_name=workbook_name, dialect=dialect
        ),
        markdown_audit=build_markdown_audit(fields, datasources),
        workbook_name=workbook_name,
        dialect=dialect,
    )
