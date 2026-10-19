"""Assemble inspect JSON (counts, connections, fields, parameters, dashboards)."""

from __future__ import annotations

from engine.layout import attach_layouts
from engine.schema import (
    CalculatedField,
    InspectCounts,
    InspectSnapshot,
    ParsedWorkbook,
)


def build_inspect(
    workbook: ParsedWorkbook,
    calculated: list[CalculatedField] | None = None,
) -> InspectSnapshot:
    dashboards = attach_layouts(list(workbook.dashboards))
    connections = [conn for ds in workbook.datasources for conn in ds.connections]
    custom_sql = sum(1 for ds in workbook.datasources for rel in ds.relations if rel.custom_sql)
    hyper = sum(1 for m in workbook.archive_members if m.kind == "hyper")
    calc_count = len(calculated) if calculated is not None else len(workbook.calculated_fields)
    hidden = sum(1 for field in workbook.fields if field.hidden)
    return InspectSnapshot(
        workbook_name=workbook.workbook_name,
        counts=InspectCounts(
            datasources=len(workbook.datasources),
            fields=len(workbook.fields),
            hidden_fields=hidden,
            calculated_fields=calc_count,
            parameters=len(workbook.parameters),
            dashboards=len(dashboards),
            archive_members=len(workbook.archive_members),
            custom_sql=custom_sql,
            hyper_files=hyper,
        ),
        connections=connections,
        fields=list(workbook.fields),
        parameters=list(workbook.parameters),
        dashboards=dashboards,
        archive_members=list(workbook.archive_members),
    )


def inspect_markdown(snapshot: InspectSnapshot) -> str:
    c = snapshot.counts
    lines = [
        f"# Inspect {snapshot.workbook_name or 'workbook'}",
        "",
        f"- datasources: {c.datasources}",
        f"- fields: {c.fields} ({c.hidden_fields} hidden)",
        f"- calculated fields: {c.calculated_fields}",
        f"- parameters: {c.parameters}",
        f"- dashboards: {c.dashboards}",
        f"- archive members: {c.archive_members}",
        f"- custom SQL: {c.custom_sql}",
        f"- hyper files: {c.hyper_files}",
        "",
        "## Connections",
        "",
    ]
    for conn in snapshot.connections:
        bits = [
            conn.class_name,
            conn.server,
            conn.dbname,
            conn.filename,
            conn.username,
            f"password={conn.password}" if conn.password else None,
        ]
        lines.append("- " + " | ".join(part for part in bits if part))
    lines.extend(["", "## Parameters", ""])
    for param in snapshot.parameters:
        lines.append(f"- {param.caption} ({param.datatype or 'unknown'}) value={param.value or ''}")
    lines.extend(["", "## Dashboards", ""])
    for dash in snapshot.dashboards:
        lines.append(f"- {dash.name} ({dash.width or '?'}×{dash.height or '?'})")
    lines.append("")
    return "\n".join(lines)
