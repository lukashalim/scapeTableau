"""Assemble sql_script and markdown_audit from transpile outputs."""

from __future__ import annotations

import re
from pathlib import Path

from engine.measure_names import (
    build_conversion_summary,
    dax_measure_names_block,
    dax_raw_measure_assignments,
    sql_measure_names_comments,
)
from engine.schema import (
    CalculatedField,
    ColumnField,
    DashboardInfo,
    DatasourceInfo,
    ExtractInventory,
    InspectSnapshot,
    MeasureNamesUsage,
    ParameterInfo,
    ParseStatus,
    TranspileResult,
)

_CONSTANT_CASE_RE = re.compile(
    r"CASE\s+WHEN\s+'((?:''|[^'])*)'\s*=\s*'\1'\s+THEN",
    re.IGNORECASE,
)


def _sql_ident(name: str, dialect: str = "ansi") -> str:
    from engine.transpiler import quote_ident

    return quote_ident(name, dialect)


def _view_name(field: CalculatedField) -> str:
    """Preserve Tableau captions inside quotes (including ``- ( ) ? %``)."""

    base = (field.caption or field.name or "calc").strip()
    return base or "calc"


def _datasource_view_name(ds: DatasourceInfo, *, used: set[str] | None = None) -> str:
    """Unique view name per datasource — caption, else hyper file stem, else name."""

    candidates = [
        (ds.caption or "").strip(),
        _hyper_file_stem(ds) or "",
        (ds.name or "").strip(),
    ]
    for base in candidates:
        if not base or base.lower() == "parameters":
            continue
        if used is not None and base in used:
            continue
        if used is not None:
            used.add(base)
        return base
    fallback = (ds.name or "datasource").strip() or "datasource"
    if used is not None:
        n = 2
        name = fallback
        while name in used:
            name = f"{fallback}_{n}"
            n += 1
        used.add(name)
        return name
    return fallback


def _hyper_file_stem(ds: DatasourceInfo) -> str | None:
    for conn in ds.connections:
        class_name = (conn.class_name or "").lower()
        dbname = conn.dbname or conn.filename
        if class_name == "hyper" and dbname:
            stem = Path(dbname).stem
            # Tableau often appends "leaf" to extract filenames.
            if stem.lower().endswith("leaf") and len(stem) > 4:
                stem = stem[:-4]
            return stem or None
    return None


def _is_generic_extract_table(parts: list[str]) -> bool:
    lowered = [p.lower() for p in parts]
    return lowered in (["extract"], ["extract", "extract"])


def _primary_relation(ds: DatasourceInfo, dialect: str = "ansi") -> str:
    from engine.transpiler import quote_ident, quote_table_ref, split_tableau_ident

    for rel in ds.relations:
        raw = rel.table or rel.name
        parts = split_tableau_ident(raw)
        if not parts:
            continue
        # Multiple Hyper datasources share [Extract].[Extract] — use caption / file stem
        # so FROM targets stay distinct across views in one script.
        if _is_generic_extract_table(parts):
            unique = (ds.caption or "").strip() or _hyper_file_stem(ds) or (rel.name or "").strip()
            if unique and unique.lower() != "extract":
                return quote_ident(unique, dialect)
        return quote_table_ref(raw, dialect)
    if ds.caption:
        return quote_ident(ds.caption, dialect)
    stem = _hyper_file_stem(ds)
    if stem:
        return quote_ident(stem, dialect)
    return quote_ident(ds.name or "Orders", dialect)


def _datasource_comment(ds: DatasourceInfo, dialect: str = "ansi") -> list[str]:
    from engine.transpiler import quote_table_ref

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
            raw = rel.table or rel.name or ""
            quoted = quote_table_ref(raw, dialect)
            lines.append(f"--   relation: {rel.type or 'table'} {raw} → {quoted}")
    return lines


def _format_param_value(info: ParameterInfo) -> str:
    from engine.transpiler import normalize_number_literal

    raw = info.value if info.value not in (None, "") else (info.formula or "")
    text = str(raw).strip()
    if (info.datatype or "").lower() in {"real", "float", "double"} or re.fullmatch(
        r"-?\d+\.\d+", text
    ):
        return normalize_number_literal(text.strip('"'))
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1].replace("\\%", "%").replace('\\"', '"')
    return text


def _format_allowed(info: ParameterInfo) -> str:
    from engine.transpiler import normalize_number_literal

    if info.allowed_values:
        shown = []
        for val in info.allowed_values[:12]:
            cleaned = val.replace("\\%", "%")
            shown.append(cleaned)
        extra = len(info.allowed_values) - len(shown)
        body = ", ".join(shown)
        if extra > 0:
            body += f", … (+{extra} more)"
        return body
    if info.domain_type == "range" or info.range_min is not None or info.range_max is not None:
        lo = info.range_min if info.range_min is not None else "?"
        hi = info.range_max if info.range_max is not None else "?"
        try:
            lo = normalize_number_literal(str(lo)) if lo != "?" else lo
            hi = normalize_number_literal(str(hi)) if hi != "?" else hi
        except Exception:
            pass
        return f"range {lo}..{hi}"
    return "(any)"


def _parameter_comment_block(parameters: list[ParameterInfo] | None) -> list[str]:
    lines = [
        "-- Parameters (inlined at current workbook values; Power BI what-if later)",
    ]
    if not parameters:
        lines.append("--   (none)")
        lines.append("")
        return lines
    for info in parameters:
        current = _format_param_value(info)
        allowed = _format_allowed(info)
        lines.append(
            f"--   {info.caption}: current={current!s}; allowed={allowed}"
        )
    lines.append("")
    return lines


def _emit_skipped(lines: list[str], field: CalculatedField) -> None:
    lines.append(
        f"-- SKIPPED [{field.status.value}] {field.caption}: {field.error or 'see SQL fallback'}"
    )
    for sql_line in field.sql.splitlines() or [field.sql]:
        if sql_line.startswith("--"):
            lines.append(sql_line)
        else:
            lines.append(f"-- {sql_line}")
    lines.append("")


def _flag_constant_case(sql: str) -> bool:
    return bool(_CONSTANT_CASE_RE.search(sql or ""))


def _workbook_has_fixed_lod(fields: list[CalculatedField]) -> bool:
    """True if any calc uses explicit FIXED or bare ``{SUM(...)}`` FIXED-none."""

    for field in fields:
        formula = field.formula or ""
        if re.search(r"\{\s*FIXED\b", formula, re.IGNORECASE):
            return True
        if re.search(
            r"\{\s*(SUM|AVG|MIN|MAX|COUNT|COUNTD|ATTR)\s*\(",
            formula,
            re.IGNORECASE,
        ):
            return True
    return False


FIXED_SLICER_WARNING = (
    "Tableau FIXED ignores dimension filters except context filters; "
    "Power BI slicers will affect results unless handled."
)


def build_sql_script(
    fields: list[CalculatedField],
    datasources: list[DatasourceInfo],
    *,
    workbook_name: str | None = None,
    dialect: str = "ansi",
    script_style: str = "sql",
    parameters: list[ParameterInfo] | None = None,
    measure_names: list[MeasureNamesUsage] | None = None,
) -> str:
    """Emit one SQL view per datasource at source grain.

    Row-level calcs (and LOD window/subquery expressions) become extra SELECT
    columns. Bare aggregates go under ``-- Aggregates / LOD`` comments. Failed
    / unmapped fields stay ``-- SKIPPED``.
    """

    lines: list[str] = [
        "-- EscapeTableau generated SQL",
        "-- Dialect: " + dialect,
    ]
    if workbook_name:
        lines.append(f"-- Source workbook: {workbook_name}")
    lines.append(
        "-- Files were parsed in memory; review LOD / unmapped functions before running."
    )
    lines.append("-- One view per datasource (SELECT * + row-level calc columns).")
    if _workbook_has_fixed_lod(fields):
        lines.append(f"-- WARNING: {FIXED_SLICER_WARNING}")
    lines.append("")
    lines.extend(_parameter_comment_block(parameters))
    lines.extend(sql_measure_names_comments(list(measure_names or [])))
    for ds in datasources:
        lines.extend(_datasource_comment(ds, dialect))
        lines.append("")

    ds_by_caption = {ds.caption: ds for ds in datasources if ds.caption}
    ds_by_name = {ds.name: ds for ds in datasources}
    used_view_names: set[str] = set()

    def _fields_for(ds: DatasourceInfo) -> list[CalculatedField]:
        keys = {ds.name, ds.caption}
        return [f for f in fields if f.datasource in keys]

    emitted_fields: set[int] = set()
    for ds in datasources:
        if (ds.name or "").lower() == "parameters":
            continue
        group = _fields_for(ds)
        if not group:
            continue
        for field in group:
            emitted_fields.add(id(field))

        row_fields = [
            f
            for f in group
            if f.status == ParseStatus.ok and (f.sql_kind or "row") == "row"
        ]
        agg_fields = [
            f
            for f in group
            if f.status == ParseStatus.ok and f.sql_kind == "aggregate"
        ]
        skipped = [f for f in group if f.status != ParseStatus.ok]

        view = _sql_ident(_datasource_view_name(ds, used=used_view_names), dialect)
        from_rel = _primary_relation(ds, dialect)
        if script_style == "dbt":
            lines.append("{{ config(materialized='view') }}")
        lines.append(f"CREATE VIEW {view} AS")
        lines.append("SELECT")
        select_lines = ["  *"]
        for field in row_fields:
            col = _sql_ident(_view_name(field), dialect)
            select_lines.append(f"  {field.sql} AS {col}")
        lines.append(",\n".join(select_lines))
        lines.append(f"FROM {from_rel};")
        lines.append("")

        if agg_fields:
            lines.append("-- Aggregates / LOD")
            for field in agg_fields:
                col = _sql_ident(_view_name(field), dialect)
                if _flag_constant_case(field.sql):
                    lines.append(
                        f"-- NOTE: CASE is constant after parameter inline ({field.caption})"
                    )
                lines.append(f"-- {col}: {field.sql}")
            lines.append("")

        for field in skipped:
            _emit_skipped(lines, field)

    # Orphan fields (datasource missing from workbook list)
    for field in fields:
        if id(field) in emitted_fields:
            continue
        if field.status != ParseStatus.ok:
            _emit_skipped(lines, field)
            continue
        ds = ds_by_name.get(field.datasource) or ds_by_caption.get(field.datasource)
        from_rel = _primary_relation(ds, dialect) if ds else _sql_ident("Orders", dialect)
        view = _sql_ident(_view_name(field), dialect)
        if (field.sql_kind or "row") == "aggregate":
            lines.append("-- Aggregates / LOD")
            if _flag_constant_case(field.sql):
                lines.append(
                    f"-- NOTE: CASE is constant after parameter inline ({field.caption})"
                )
            lines.append(f"-- {view}: {field.sql}")
            lines.append("")
            continue
        if script_style == "dbt":
            lines.append("{{ config(materialized='view') }}")
        lines.append(f"CREATE VIEW {view} AS")
        lines.append("SELECT")
        lines.append("  *,")
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
    measure_names: list[MeasureNamesUsage] | None = None,
    workbook_fields: list[ColumnField] | None = None,
) -> str:
    from engine.transpiler import param_dax_literal, param_measure_name

    lines = ["// BIEscape generated DAX"]
    if workbook_name:
        lines.append(f"// Source workbook: {workbook_name}")
    if _workbook_has_fixed_lod(fields):
        lines.append(f"// WARNING: {FIXED_SLICER_WARNING}")
    table = _dax_table_name(fields)
    usages = list(measure_names or [])
    lines.extend(
        dax_measure_names_block(
            usages,
            dax_table=table,
            calculated_fields=fields,
            columns=workbook_fields,
        )
    )
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
    # Raw columns referenced by Measure Names filters (not already calculated fields)
    raw_assignments = dax_raw_measure_assignments(
        usages, dax_table=table, calculated_fields=fields
    )
    if raw_assignments:
        lines.append("")
        lines.append("// Measures from Measure Names filter (raw columns)")
        for assign in raw_assignments:
            lines.append(assign)
            lines.append("")
    _emit_section("Calculated columns", columns, kind="column")
    return "\n".join(lines).rstrip() + "\n"


def _md_cell(value: str | None) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def build_markdown_audit(
    fields: list[CalculatedField],
    datasources: list[DatasourceInfo],
    *,
    measure_names: list[MeasureNamesUsage] | None = None,
) -> str:
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
    summary = build_conversion_summary(list(measure_names or []))
    if summary.strip():
        lines.append(summary)
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
    measure_names: list[MeasureNamesUsage] | None = None,
    workbook_fields: list[ColumnField] | None = None,
) -> TranspileResult:
    usages = list(measure_names or [])
    return TranspileResult(
        calculated_fields=fields,
        datasources=datasources,
        sql_script=build_sql_script(
            fields,
            datasources,
            workbook_name=workbook_name,
            dialect=dialect,
            script_style=script_style,
            parameters=parameters,
            measure_names=usages,
        ),
        markdown_audit=build_markdown_audit(
            fields, datasources, measure_names=usages
        ),
        workbook_name=workbook_name,
        dialect=dialect,
        inspect=inspect,
        extract_inventory=extract_inventory,
        dashboards=list(dashboards or (inspect.dashboards if inspect else [])),
        dax_script=build_dax_script(
            fields,
            workbook_name=workbook_name,
            parameters=parameters,
            measure_names=usages,
            workbook_fields=workbook_fields,
        ),
        target=target,
        measure_names=usages,
        conversion_summary=build_conversion_summary(usages),
    )
