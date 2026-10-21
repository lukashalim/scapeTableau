"""Assemble a Power BI Project (.pbip) folder zip from a TranspileResult."""

from __future__ import annotations

import json
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from engine.pbip.conversion import ConversionEntry, ConversionReport, disposition_for_field
from engine.pbip.data_csv import collect_data_csvs, safe_csv_filename
from engine.pbip.dax_polish import (
    percent_format_string,
    polish_dax,
    uniquify_measure_name,
)
from engine.pbip.desktop_paths import (
    ensure_desktop_project_name,
    looks_like_tableau_build_id,
    plan_project_name,
)
from engine.pbip.m_partitions import plan_m_partition, tableau_type_to_m
from engine.pbip.pbir import build_pbir_files, visual_caveats
from engine.pbip.render import (
    indent_block,
    load_template,
    render_json_template,
    render_template,
    tmdl_ident,
)
from engine.pbip.validate import validate_pbip_files

_THEME_PATH = Path(__file__).resolve().parent / "themes" / "CY24SU10.json"
from engine.schema import (
    CalculatedField,
    DatasourceInfo,
    JoinInfo,
    ParameterInfo,
    ParseStatus,
    TranspileResult,
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _project_name(
    workbook_name: str | None,
    *,
    source_stem: str | None = None,
) -> str:
    """Prefer a human file stem over Tableau ``source-build`` version strings."""

    preferred = source_stem
    if not preferred or looks_like_tableau_build_id(preferred):
        preferred = workbook_name if not looks_like_tableau_build_id(workbook_name) else None
    if not preferred:
        preferred = source_stem or workbook_name or "BIEscape"
    name = plan_project_name(preferred, fallback="BIEscape", max_len=64)
    # Forecast unzip under a typical Downloads path (cyphou desktop_paths pattern)
    return ensure_desktop_project_name("C:/Users/User/Downloads", name)


def _lineage() -> str:
    return str(uuid.uuid4())


def _platform_json(*, item_type: str, display_name: str) -> str:
    payload = {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
            "platformProperties/2.0.0/schema.json"
        ),
        "metadata": {"type": item_type, "displayName": display_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _pbi_table_name(ds: DatasourceInfo) -> str:
    caption = (ds.caption or "").strip()
    if caption and not caption.lower().startswith("dataengine."):
        return caption
    name = (ds.name or "").strip()
    if name and not name.lower().startswith("dataengine.") and name.lower() != "extract":
        return name
    return caption or name or "Table"


def _datatype_tmdl(datatype: str | None) -> str:
    dt = (datatype or "string").lower()
    if dt in {"integer", "int"}:
        return "int64"
    if dt in {"real", "float", "double", "number"}:
        return "double"
    if dt == "date":
        return "dateTime"
    if dt in {"datetime", "timestamp"}:
        return "dateTime"
    if dt in {"boolean", "bool"}:
        return "boolean"
    return "string"


def _format_string(datatype: str | None, role: str | None) -> str | None:
    dt = (datatype or "").lower()
    if dt in {"real", "float", "double", "number"}:
        return "#,0.00"
    if dt in {"integer", "int"}:
        return "#,0"
    if dt == "date":
        return "yyyy-mm-dd"
    if dt in {"datetime", "timestamp"}:
        return "General Date"
    if (role or "").lower() == "measure":
        return "#,0.00"
    return None


def _is_key_like(name: str, role: str | None) -> bool:
    lowered = name.lower()
    if (role or "").lower() == "dimension":
        return True
    return any(tok in lowered for tok in (" id", "_id", "key", "order id", "row id", "customer id"))


def _source_columns(ds: DatasourceInfo) -> list[tuple[str, str | None, str | None]]:
    """(caption, datatype, role) for non-calculated columns."""

    out: list[tuple[str, str | None, str | None]] = []
    seen: set[str] = set()
    for field in ds.fields:
        if field.calculated:
            continue
        caption = (field.caption or field.name or "").strip("[]")
        if not caption or caption in seen:
            continue
        seen.add(caption)
        out.append((caption, field.datatype, field.role))
    return out


def _fields_for_ds(result: TranspileResult, ds: DatasourceInfo) -> list[CalculatedField]:
    keys = {ds.name, ds.caption}
    return [f for f in result.calculated_fields if f.datasource in keys]


def _measure_eligible(field: CalculatedField) -> bool:
    if field.dax_status not in (None, ParseStatus.ok):
        return False
    kind = field.dax_kind or ("measure" if field.sql_kind == "aggregate" else "calculated_column")
    if kind != "measure":
        return False
    # Stage 2 table calcs: SQL skipped but DAX ok → still a measure.
    if field.status not in (ParseStatus.ok, ParseStatus.skipped):
        return False
    dax = (field.dax or "").strip()
    if not dax or dax.startswith("//"):
        return False
    upper = dax.upper()
    tokens = (
        "SUM(",
        "AVERAGE(",
        "AVG(",
        "MIN(",
        "MAX(",
        "COUNT(",
        "DISTINCTCOUNT(",
        "DIVIDE(",
        "CALCULATE(",
        "AVERAGEX(",
        "SUMX(",
        "RANKX(",
        "COUNTROWS(",
        "IF(",
        "SWITCH(",
        "COALESCE(",
    )
    return any(t in upper for t in tokens)


def _column_block(caption: str, datatype: str | None, role: str | None) -> str:
    data_type = _datatype_tmdl(datatype)
    summarize = "none" if _is_key_like(caption, role) else ("sum" if data_type in {"int64", "double"} else "none")
    fmt = _format_string(datatype, role)
    lines = [
        f"\tcolumn {tmdl_ident(caption)}",
        f"\t\tdataType: {data_type}",
        f"\t\tlineageTag: {_lineage()}",
        f"\t\tsummarizeBy: {summarize}",
        f"\t\tsourceColumn: {caption}",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
    ]
    if fmt:
        lines.insert(3, f"\t\tformatString: {fmt}")
    if data_type == "dateTime" and (datatype or "").lower() == "date":
        lines.append("")
        lines.append("\t\tannotation UnderlyingDateTimeDataType = Date")
    return "\n".join(lines)


def _calc_column_block(field: CalculatedField) -> str | None:
    if field.dax_kind == "measure":
        return None
    if field.dax_status not in (None, ParseStatus.ok):
        return None
    dax = (field.dax or "").strip()
    if not dax or dax.startswith("//"):
        return None
    data_type = _datatype_tmdl(field.datatype)
    fmt = _format_string(field.datatype, field.role)
    lines = [
        f"\tcolumn {tmdl_ident(field.caption)} = {dax}",
        f"\t\tdataType: {data_type}",
        f"\t\tlineageTag: {_lineage()}",
        "\t\tsummarizeBy: none",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
    ]
    if fmt:
        lines.insert(3, f"\t\tformatString: {fmt}")
    return "\n".join(lines)


def _measure_block(field: CalculatedField, *, measure_name: str | None = None) -> str:
    dax = polish_dax((field.dax or "").strip())
    pct = percent_format_string(field.caption, field.datatype, dax)
    fmt = pct or _format_string(field.datatype, "measure") or "#,0.00"
    folder = "Converted measures"
    if "FIXED" in (field.formula or "").upper() or "ALLEXCEPT" in dax.upper() or "ALL(" in dax.upper():
        folder = "LOD"
    if "RANKX" in dax.upper() or "ALLSELECTED" in dax.upper():
        folder = "Table calculations"
    caption = measure_name or field.caption
    return "\n".join(
        [
            f"\tmeasure {tmdl_ident(caption)} = {dax}",
            f"\t\tformatString: {fmt}",
            f"\t\tlineageTag: {_lineage()}",
            f"\t\tdisplayFolder: {folder}",
        ]
    )


def _partition_block(table_name: str, m_expr: str) -> str:
    indented = indent_block(m_expr.strip(), spaces=3)
    return (
        f"\tpartition {tmdl_ident(table_name)} = m\n"
        f"\t\tmode: import\n"
        f"\t\tsource =\n"
        f"{indented}"
    )


def _relationships_tmdl(joins: list[JoinInfo], table_name_by_ds: dict[str, str]) -> str:
    """Serialize relationships.tmdl — Desktop rejects empty lines as InvalidLineType.

    When there are no joins, emit a truly empty file (cyphou/TableauToPowerBI pattern).
    Do not write ``///`` comments here; they leave a blank line that fails open.
    """

    if not joins:
        return ""
    blocks: list[str] = []
    for i, join in enumerate(joins, start=1):
        left_table = table_name_by_ds.get(join.left_datasource) or join.left_datasource
        right_table = table_name_by_ds.get(join.right_datasource) or join.right_datasource
        blocks.append(
            "\n".join(
                [
                    f"relationship Relationship{i}",
                    f"\tfromColumn: {tmdl_ident(left_table)}.{tmdl_ident(join.left_column)}",
                    f"\ttoColumn: {tmdl_ident(right_table)}.{tmdl_ident(join.right_column)}",
                    "\tcrossFilteringBehavior: oneDirection",
                    "\tfromCardinality: many",
                    "\ttoCardinality: one",
                ]
            )
        )
    # Single trailing newline; no leading/extra blank lines (Desktop InvalidLineType: Empty!)
    return "\n\n".join(blocks) + "\n"


def _datafolder_expression() -> str:
    """Shared folder next to the .pbip — user sets absolute path after unzip."""

    return (
        'expression DataFolder = "Data" meta '
        '[IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
    )


def _expression_blocks(params: list[tuple[str, str, str]], workbook_params: list[ParameterInfo]) -> str:
    chunks: list[str] = [_datafolder_expression()]
    for name, _kind, expr in params:
        if name == "DataFolder":
            continue
        chunks.append(f"expression {name} = {expr}")
    for info in workbook_params:
        pname = re.sub(r"[^A-Za-z0-9_]", "_", info.caption) + "_Param"
        raw = info.value if info.value not in (None, "") else (info.formula or "")
        text = str(raw).strip().strip('"')
        dt = (info.datatype or "").lower()
        if dt in {"integer", "int", "real", "float", "double"}:
            try:
                num = float(text)
                start = num
                end = num if dt in {"integer", "int"} else num
                step = 1 if dt in {"integer", "int"} else (num / 10 if num else 1)
                chunks.append(
                    f"expression {pname} =\n"
                    f"\tlet\n"
                    f"\t    Source = {start}\n"
                    f"\tin\n"
                    f"\t    Source meta [IsParameterQuery=true, Type=\"Number\"]"
                )
                # Also document GENERATESERIES what-if table intent in report
            except ValueError:
                chunks.append(
                    f'expression {pname} = "{text}" meta [IsParameterQuery=true, Type="Text"]'
                )
        else:
            chunks.append(
                f'expression {pname} = "{text}" meta [IsParameterQuery=true, Type="Text"]'
            )
    if not chunks:
        return "/// No shared expressions\n"
    return "\n\n".join(chunks) + "\n"


def _what_if_tables(parameters: list[ParameterInfo]) -> list[tuple[str, str]]:
    """Return (table_name, tmdl) for numeric GENERATESERIES what-if (Franssens pattern)."""

    out: list[tuple[str, str]] = []
    for info in parameters:
        caption = info.caption or info.name
        table = (caption or "Parameter").strip() or "Parameter"
        # Keep readable table names (spaces ok in TMDL quotes) — Franssens style
        dt = (info.datatype or "").lower()
        raw = info.value if info.value not in (None, "") else (info.formula or "0")
        text = str(raw).strip().strip('"')
        if dt in {"integer", "int", "real", "float", "double"}:
            try:
                num = float(text)
            except ValueError:
                num = 0
            lo = 0 if num >= 0 else num
            hi = max(num * 2, 10) if num else 10
            step = 1 if dt in {"integer", "int"} else max(abs(num) / 20 if num else 0.1, 0.01)
            # Percent-looking parameters get % format (Commission Rate pattern)
            is_pct = any(tok in table.lower() for tok in ("rate", "percent", "%", "pct"))
            col_fmt = '0.0"%"' if is_pct else ("#,0" if dt in {"integer", "int"} else "#,0.00")
            meas_fmt = col_fmt
            measure_name = f"{caption} Value"
            col_name = caption
            tmdl = f"""/// What-If parameter (Tableau '{caption}'). Franssens GENERATESERIES pattern.
table '{table.replace("'", "''")}'

	measure '{measure_name.replace("'", "''")}' = SELECTEDVALUE('{table.replace("'", "''")}'[{col_name.replace("'", "''")}], {num})
		formatString: {meas_fmt}
		displayFolder: Parameters

	column '{col_name.replace("'", "''")}'
		dataType: double
		formatString: {col_fmt}
		summarizeBy: none
		sourceColumn: [Value]

		annotation SummarizationSetBy = Automatic

	partition '{table.replace("'", "''")}' = calculated
		mode: import
		source = GENERATESERIES({lo}, {hi}, {step})

	annotation PBI_ResultType = Table
"""
            out.append((table, tmdl))
        elif info.allowed_values:
            rows = ", ".join(f'{{"{v.replace(chr(34), chr(34)+chr(34))}"}}' for v in info.allowed_values[:50])
            measure_name = f"{caption} Value"
            default = text.replace('"', '""')
            safe = re.sub(r"[^A-Za-z0-9_]", "_", caption) + "_WhatIf"
            tmdl = f"""table '{safe}'
	lineageTag: {_lineage()}

	column Value
		dataType: string
		lineageTag: {_lineage()}
		summarizeBy: none
		sourceColumn: Value

	measure '{measure_name.replace("'", "''")}' = SELECTEDVALUE('{safe}'[Value], "{default}")
		lineageTag: {_lineage()}
		displayFolder: Parameters

	partition '{safe}' = m
		mode: import
		source =
			let
			    Source = #table({{"Value"}}, {{{rows}}})
			in
			    Source
"""
            out.append((safe, tmdl))
    return out


def build_pbip_files(
    result: TranspileResult,
    *,
    source_stem: str | None = None,
    source: object | None = None,
) -> tuple[dict[str, str | bytes], ConversionReport]:
    """Return mapping of zip paths → file contents, plus conversion report."""

    name = _project_name(result.workbook_name, source_stem=source_stem)
    sm_folder = f"{name}.SemanticModel"
    report_folder = f"{name}.Report"
    report = ConversionReport()
    report.notes.append(
        "SQL script remains available from the transpile API / SQL download; PBIP is the default package."
    )

    files: dict[str, str | bytes] = {}
    table_refs: list[str] = []
    query_order: list[str] = []
    all_params: list[tuple[str, str, str]] = []
    table_name_by_ds: dict[str, str] = {}
    measure_blocks: list[str] = []
    used_measure_names: set[str] = set()

    parameters = list(result.inspect.parameters) if result.inspect else []
    for info in parameters:
        report.parameter_notes.append(
            f"Parameter '{info.caption}' → what-if / disconnected table + SELECTEDVALUE "
            f"(Franssens GENERATESERIES pattern; was inlined as workbook constant in SQL)."
        )

    for caveat in visual_caveats():
        report.notes.append(caveat)
    report.notes.append(
        "TMDL/PBIR templates follow vendored Franssens patterns "
        "(compatLevel 1702, pbism 4.2, PBIR definition/ pages). See docs/third-party-pbip.md."
    )
    report.notes.append(
        "Desktop openability extras adapted from cyphou/TableauToPowerBI (MIT): "
        "path-budget naming, ==→= DAX heal, TMDL ''-safe validation. See THIRD_PARTY_NOTICES."
    )
    try:
        from engine.sqlglot_util import custom_sql_notes

        for note in custom_sql_notes(result.datasources):
            status = "ok" if note.ok else "unparsed"
            report.notes.append(
                f"Custom SQL [{status}] {note.datasource}/{note.name}: {note.detail}"
            )
    except Exception:
        pass

    # Precompute table names so Data/ CSVs and M partitions share filenames
    for ds in result.datasources:
        if (ds.name or "").lower() == "parameters":
            continue
        table_name = _pbi_table_name(ds)
        table_name_by_ds[ds.name] = table_name
        if ds.caption:
            table_name_by_ds[ds.caption] = table_name

    data_csvs, data_notes = collect_data_csvs(source, result, table_names=table_name_by_ds)
    for path, blob in data_csvs.items():
        files[path] = blob
    for note in data_notes:
        if "Hyper" in note or "hyper" in note:
            report.hyper_todos.append(note)
        else:
            report.notes.append(note)

    for ds in result.datasources:
        if (ds.name or "").lower() == "parameters":
            continue
        table_name = table_name_by_ds[ds.name]
        cols = _source_columns(ds)
        col_types = [(c, tableau_type_to_m(dt)) for c, dt, _ in cols]
        csv_name = safe_csv_filename(table_name)
        plan = plan_m_partition(
            ds,
            table_name=table_name,
            column_types=col_types,
            csv_filename=csv_name,
        )
        all_params.extend(plan.parameters)
        for note in plan.notes:
            if plan.source_kind == "hyper":
                report.hyper_todos.append(note)
            else:
                report.notes.append(note)

        column_tmdl = "\n\n".join(_column_block(c, dt, role) for c, dt, role in cols)
        calc_blocks: list[str] = []
        for field in _fields_for_ds(result, ds):
            disp, reason = disposition_for_field(field)
            kind = field.dax_kind or "calculated_column"
            polished = polish_dax(field.dax or "")
            if polished and polished != (field.dax or ""):
                field.dax = polished
            for todo in getattr(field, "dax_notes", None) or []:
                report.dax_todos.append(f"{field.caption}: {todo}")
                disp = "approximated"
                reason = (reason + "; " if reason else "") + todo
            if _measure_eligible(field):
                mname = uniquify_measure_name(field.caption, used_measure_names, table_name)
                measure_blocks.append(_measure_block(field, measure_name=mname))
                kind = "measure"
            else:
                block = _calc_column_block(field)
                if block:
                    calc_blocks.append(block)
            report.add(
                ConversionEntry(
                    caption=field.caption,
                    datasource=field.datasource,
                    original_formula=field.formula,
                    dax=polished or field.dax or "",
                    kind=kind if field.status == ParseStatus.ok or field.dax_status == ParseStatus.ok else "skipped",
                    disposition=disp,
                    reason=reason,
                )
            )

        partition = _partition_block(table_name, plan.expression)
        table_body = render_template(
            "table.tmdl",
            {
                "TABLE_NAME": table_name.replace("'", "''"),
                "TABLE_LINEAGE": _lineage(),
                "COLUMNS": column_tmdl + ("\n" if column_tmdl else ""),
                "CALC_COLUMNS": ("\n\n".join(calc_blocks) + "\n") if calc_blocks else "",
                "PARTITION": partition,
            },
        )
        safe_file = _SAFE_NAME.sub("_", table_name) or "Table"
        files[f"{sm_folder}/definition/tables/{safe_file}.tmdl"] = table_body
        table_refs.append(f"ref table {tmdl_ident(table_name)}")
        query_order.append(table_name)

    # What-if parameter tables
    for table, tmdl in _what_if_tables(parameters):
        safe_whatif = _SAFE_NAME.sub("_", table) or "Parameter"
        files[f"{sm_folder}/definition/tables/{safe_whatif}.tmdl"] = tmdl
        table_refs.append(f"ref table {tmdl_ident(table)}")
        query_order.append(table)

    measures_tmdl = render_template(
        "measures_table.tmdl",
        {
            "TABLE_LINEAGE": _lineage(),
            "DUMMY_LINEAGE": _lineage(),
            "MEASURES": ("\n\n".join(measure_blocks) + "\n") if measure_blocks else "\t/// (no measures)\n",
        },
    )
    files[f"{sm_folder}/definition/tables/__Measures.tmdl"] = measures_tmdl
    query_order.append("__Measures")

    files[f"{sm_folder}/definition/database.tmdl"] = load_template("database.tmdl")
    files[f"{sm_folder}/definition/model.tmdl"] = render_template(
        "model.tmdl",
        {
            "TABLE_REFS": "\n".join(table_refs),
            "QUERY_ORDER_JSON": json.dumps(query_order),
        },
    )
    joins = list(getattr(result, "joins", None) or [])
    if not joins:
        report.notes.append(
            "No Tableau joins discovered — relationships.tmdl left empty "
            "(Desktop rejects comment/blank-only TMDL in that file)."
        )
    files[f"{sm_folder}/definition/relationships.tmdl"] = _relationships_tmdl(
        joins, table_name_by_ds
    )
    files[f"{sm_folder}/definition/expressions.tmdl"] = render_template(
        "expressions.tmdl",
        {"EXPRESSION_BLOCKS": _expression_blocks(all_params, parameters)},
    )
    files[f"{sm_folder}/definition.pbism"] = render_json_template("definition.pbism.json", {})
    files[f"{sm_folder}/.platform"] = _platform_json(item_type="SemanticModel", display_name=name)

    files[f"{report_folder}/definition.pbir"] = render_json_template(
        "definition.pbir.json",
        {"SEMANTIC_MODEL_FOLDER": sm_folder},
    )
    files[f"{report_folder}/.platform"] = _platform_json(item_type="Report", display_name=name)
    # Base theme referenced by report.definition.json — must exist on disk
    if _THEME_PATH.is_file():
        files[
            f"{report_folder}/StaticResources/SharedResources/BaseThemes/CY24SU10.json"
        ] = _THEME_PATH.read_text(encoding="utf-8")
    # Stage 3: real PBIR definition/ pages + visuals (Franssens layout)
    files.update(build_pbir_files(result, report_folder, table_name_by_ds=table_name_by_ds))

    files[f"{name}.pbip"] = render_json_template(
        "project.pbip.json",
        {"REPORT_FOLDER": report_folder},
    )
    files["CONVERSION_REPORT.md"] = report.to_markdown()
    files[".gitignore"] = "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n"

    # Post-build validator — fail the build on schema / missing-file / bad DAX refs
    validate_pbip_files(files, fail=True)
    return files, report


def build_pbip_zip(
    result: TranspileResult,
    *,
    source_stem: str | None = None,
    source: object | None = None,
) -> bytes:
    files, _report = build_pbip_files(result, source_stem=source_stem, source=source)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in sorted(files.items()):
            if isinstance(content, bytes):
                zf.writestr(path, content)
            else:
                zf.writestr(path, content.encode("utf-8"))
    return buf.getvalue()


def write_pbip_dir(
    result: TranspileResult,
    dest: Path,
    *,
    source: object | None = None,
) -> ConversionReport:
    files, report = build_pbip_files(result, source=source)
    dest.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return report
