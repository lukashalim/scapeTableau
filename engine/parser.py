"""In-memory .twb / .twbx ingest using xml.etree and zipfile."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree as ET

from engine.redact import redact_connect_fragment, redact_password_attr
import re

from engine.measure_names import (
    detect_measure_names_usages,
    is_mn_mv_token,
    scrub_worksheet_mn_mv,
)
from engine.schema import (
    ArchiveKind,
    ArchiveMember,
    ColumnField,
    ConnectionInfo,
    DashboardInfo,
    DashboardZone,
    DatasourceInfo,
    JoinInfo,
    ParameterInfo,
    ParsedWorkbook,
    RawCalculatedField,
    RelationInfo,
    WorkbookParseError,
    WorksheetInfo,
)

Source = str | Path | bytes | BinaryIO
ZIP_MAGIC = b"PK"


def _local_tag(tag: str | None) -> str:
    if not tag:
        return ""
    return tag.split("}")[-1]


def _read_bytes(source: Source) -> tuple[bytes, str | None]:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source), None
    if isinstance(source, Path):
        try:
            return source.read_bytes(), source.name
        except OSError as exc:
            raise WorkbookParseError(f"Unable to read workbook: {exc}") from exc
    if isinstance(source, str):
        try:
            return Path(source).read_bytes(), Path(source).name
        except OSError as exc:
            raise WorkbookParseError(f"Unable to read workbook: {exc}") from exc
    read = getattr(source, "read", None)
    if not callable(read):
        raise WorkbookParseError("Unsupported workbook source type")
    data = read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    name = getattr(source, "name", None)
    filename = Path(name).name if isinstance(name, str) else None
    return bytes(data), filename


def _is_zip(data: bytes, filename: str | None) -> bool:
    if data.startswith(ZIP_MAGIC):
        return True
    if filename and filename.lower().endswith((".twbx", ".tdsx")):
        return True
    return False


def is_hyper_bytes(data: bytes, filename: str | None = None) -> bool:
    """True for a standalone .hyper extract (filename or Hyper magic)."""

    if filename and filename.lower().endswith(".hyper"):
        return True
    return data.startswith(b"Hyper")


def member_kind(path: str) -> ArchiveKind:
    lower = path.lower()
    if lower.endswith(".twb"):
        return "twb"
    if lower.endswith(".csv") or lower.endswith(".tsv"):
        return "csv"
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        return "xlsx"
    if lower.endswith(".hyper"):
        return "hyper"
    return "other"


def list_archive_members(data: bytes) -> list[ArchiveMember]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members: list[ArchiveMember] = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                members.append(
                    ArchiveMember(path=info.filename, size=int(info.file_size), kind=member_kind(info.filename))
                )
            return members
    except zipfile.BadZipFile as exc:
        raise WorkbookParseError("Invalid .twbx archive") from exc


def _twb_from_zip(data: bytes) -> tuple[bytes, list[ArchiveMember]]:
    members = list_archive_members(data)
    twb_names = [m.path for m in members if m.kind == "twb"]
    if not twb_names:
        raise WorkbookParseError("Packaged workbook does not contain a .twb file")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return zf.read(twb_names[0]), members
    except zipfile.BadZipFile as exc:
        raise WorkbookParseError("Invalid .twbx archive") from exc


def _text_content(el: ET.Element) -> str:
    parts: list[str] = [el.text or ""]
    for child in el:
        parts.append(_text_content(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _iter_direct_datasources(root: ET.Element) -> list[ET.Element]:
    for child in list(root):
        if _local_tag(child.tag) == "datasources":
            return [el for el in list(child) if _local_tag(el.tag) == "datasource"]
    return [el for el in root.iter() if _local_tag(el.tag) == "datasource"]


def _column_name_key(raw: str | None) -> str:
    if not raw:
        return ""
    name = raw.strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name


def _as_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _hidden_flag(el: ET.Element) -> bool:
    raw = (el.get("hidden") or "").strip().lower()
    return raw in {"true", "1", "yes"}


def _parse_connection(el: ET.Element) -> ConnectionInfo:
    return ConnectionInfo(
        class_name=redact_connect_fragment(el.get("class")),
        dbname=redact_connect_fragment(el.get("dbname")),
        server=redact_connect_fragment(el.get("server")),
        filename=redact_connect_fragment(el.get("filename")),
        username=redact_connect_fragment(el.get("username") or el.get("user")),
        password=redact_password_attr(el.get("password")),
        warehouse=redact_connect_fragment(el.get("warehouse")),
        schema_name=redact_connect_fragment(el.get("schema")),
    )


def _parse_zone(el: ET.Element) -> DashboardZone:
    children = [_parse_zone(child) for child in list(el) if _local_tag(child.tag) == "zone"]
    return DashboardZone(
        type=el.get("type"),
        name=el.get("name") or el.get("id"),
        x=_as_int(el.get("x")),
        y=_as_int(el.get("y")),
        w=_as_int(el.get("w")),
        h=_as_int(el.get("h")),
        children=children,
    )


def _parse_dashboards(root: ET.Element) -> list[DashboardInfo]:
    dashboards: list[DashboardInfo] = []
    for el in root.iter():
        if _local_tag(el.tag) != "dashboard":
            continue
        width = None
        height = None
        zones: list[DashboardZone] = []
        for child in el.iter():
            tag = _local_tag(child.tag)
            if tag == "size" and width is None:
                width = _as_int(child.get("maxwidth") or child.get("minwidth") or child.get("width"))
                height = _as_int(child.get("maxheight") or child.get("minheight") or child.get("height"))
        for child in list(el):
            if _local_tag(child.tag) in {"zones", "zone"}:
                if _local_tag(child.tag) == "zone":
                    zones.append(_parse_zone(child))
                else:
                    zones.extend(_parse_zone(z) for z in list(child) if _local_tag(z.tag) == "zone")
        dashboards.append(
            DashboardInfo(
                name=el.get("name") or "dashboard",
                width=width,
                height=height,
                zones=zones,
            )
        )
    return dashboards


def _parse_datasource(
    ds: ET.Element,
) -> tuple[DatasourceInfo, list[RawCalculatedField], dict[str, str], list[ParameterInfo], list[ColumnField]]:
    name = ds.get("name") or "datasource"
    caption = ds.get("caption")
    is_parameters = name.lower() == "parameters"

    connections: list[ConnectionInfo] = []
    relations: list[RelationInfo] = []
    seen_conn: set[tuple[str | None, str | None, str | None, str | None]] = set()

    for el in ds.iter():
        tag = _local_tag(el.tag)
        if tag == "connection":
            info = _parse_connection(el)
            key = (info.class_name, info.dbname, info.server, info.filename)
            if any(key) and key not in seen_conn:
                seen_conn.add(key)
                connections.append(info)
        elif tag == "relation":
            rel_type = el.get("type")
            custom_sql = None
            if rel_type == "text":
                custom_sql = redact_connect_fragment(_text_content(el).strip() or None)
            relations.append(
                RelationInfo(
                    name=el.get("name"),
                    type=rel_type,
                    table=el.get("table"),
                    custom_sql=custom_sql,
                )
            )

    name_map: dict[str, str] = {}
    calculated: list[RawCalculatedField] = []
    fields: list[ColumnField] = []
    parameters: list[ParameterInfo] = []

    for el in ds.iter():
        if _local_tag(el.tag) != "column":
            continue
        col_name = el.get("name") or ""
        col_caption = el.get("caption") or _column_name_key(col_name)
        internal = _column_name_key(col_name)
        if internal:
            name_map[internal] = col_caption
            name_map[col_name] = col_caption
            if col_caption:
                name_map[col_caption] = col_caption

        calc = None
        for child in list(el):
            if _local_tag(child.tag) == "calculation" and child.get("formula") is not None:
                calc = child
                break
        formula = (calc.get("formula") if calc is not None else None) or None
        is_calc = calc is not None and not is_parameters
        field = ColumnField(
            name=col_name or internal,
            caption=col_caption or internal,
            datatype=el.get("datatype"),
            role=el.get("role"),
            hidden=_hidden_flag(el),
            calculated=bool(is_calc),
            formula=formula,
            datasource=name,
            datasource_caption=caption,
        )
        fields.append(field)

        if is_parameters:
            domain_type = el.get("param-domain-type")
            allowed: list[str] = []
            range_min = None
            range_max = None
            for child in list(el):
                tag = _local_tag(child.tag)
                if tag == "members":
                    for member in list(child):
                        if _local_tag(member.tag) != "member":
                            continue
                        raw_val = member.get("value")
                        if raw_val is None:
                            continue
                        # Tableau escapes as "\"Names\"" or '"\\% quota ascending"'
                        text = raw_val.strip()
                        if text.startswith('"') and text.endswith('"'):
                            text = text[1:-1].replace('\\"', '"').replace("\\%", "%")
                        allowed.append(text)
                elif tag == "range":
                    range_min = child.get("min")
                    range_max = child.get("max")
            parameters.append(
                ParameterInfo(
                    name=col_name or internal,
                    caption=col_caption or internal,
                    datatype=el.get("datatype"),
                    value=el.get("value"),
                    formula=formula,
                    datasource=name,
                    domain_type=domain_type,
                    allowed_values=allowed,
                    range_min=range_min,
                    range_max=range_max,
                )
            )

        if calc is None or is_parameters:
            continue
        calculated.append(
            RawCalculatedField(
                name=col_name or internal,
                caption=col_caption or internal,
                datatype=el.get("datatype"),
                role=el.get("role"),
                formula=formula or "",
                datasource_name=name,
                datasource_caption=caption,
            )
        )

    info = DatasourceInfo(
        name=name,
        caption=caption,
        connections=connections,
        relations=relations,
        fields=fields,
    )
    return info, calculated, name_map, parameters, fields


_SHELF_FIELD = re.compile(r"\[([^\]]+)\]")
_MEASURE_PREFIXES = {
    "sum",
    "avg",
    "min",
    "max",
    "cnt",
    "ctd",
    "count",
    "countd",
    "attr",
    "med",
    "stdev",
    "var",
    "usr",
}


def _shelf_token_name(inner: str) -> tuple[str, bool]:
    """Return (field_name, is_dimension) from a Tableau shelf token."""

    text = inner.strip()
    if ":" in text:
        parts = text.split(":")
        prefix = parts[0].lower()
        # Table-calc pills: pcto:sum:Total Profit:qk. Keep the instance name so
        # it matches the calculated field; parts[1] is the agg, not the field.
        if len(parts) >= 4 and parts[1].lower() in _MEASURE_PREFIXES:
            return text, False
        name = parts[1] if len(parts) > 1 else text
        is_dim = prefix in {"none", "nk"} or prefix not in _MEASURE_PREFIXES
        if prefix in _MEASURE_PREFIXES:
            is_dim = False
        return name, is_dim
    if text.lower().startswith("federated.") or "." in text and text.lower().startswith("sqlproxy"):
        return text, True
    return text, True


def _fields_from_shelf_text(text: str | None) -> list[tuple[str, bool]]:
    if not text:
        return []
    found: list[tuple[str, bool]] = []
    for match in _SHELF_FIELD.finditer(text):
        inner = match.group(1)
        if inner.lower().startswith("federated.") or inner.lower().startswith("sqlproxy"):
            continue
        found.append(_shelf_token_name(inner))
    return found


_TABLE_CALC_LABELS: dict[str, str] = {
    "PctTotal": "% of Total",
    "CumTotal": "Running Total",
    "WindowTotal": "Window Total",
    "Difference": "Difference",
    "PctDiff": "% Difference",
    "PctValue": "% of Value",
    "Rank": "Rank",
    "PctRank": "Percentile",
}

_ORDERING_LABELS: dict[str, str] = {
    "Rows": "Table (Across)",
    "Cols": "Table (Down)",
    "Pane": "Pane (Across)",
    "PaneAcrossThenDown": "Pane (Across then Down)",
    "PaneDownThenAcross": "Pane (Down then Across)",
    "Cell": "Cell",
    "Table": "Table (Across)",
    "TableAcrossThenDown": "Table (Across then Down)",
    "TableDownThenAcross": "Table (Down then Across)",
}

_DERIVATION_AGG: dict[str, str] = {
    "sum": "SUM",
    "avg": "AVG",
    "average": "AVG",
    "min": "MIN",
    "max": "MAX",
    "cnt": "COUNT",
    "count": "COUNT",
    "ctd": "COUNTD",
    "countd": "COUNTD",
    "attr": "ATTR",
    "med": "MEDIAN",
    "stdev": "STDEV",
    "var": "VAR",
    "usr": "AGG",
    "none": "",
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "week": "WEEK",
    "day": "DAY",
}


def _agg_from_derivation(derivation: str | None, instance_name: str | None) -> str:
    if derivation:
        mapped = _DERIVATION_AGG.get(derivation.strip().lower())
        if mapped is not None:
            return mapped
        return derivation.strip().upper()
    # Fallback: pcto:sum:Field:qk → SUM
    inner = _column_name_key(instance_name)
    if ":" in inner:
        parts = inner.split(":")
        if len(parts) >= 2:
            mapped = _DERIVATION_AGG.get(parts[1].lower())
            if mapped:
                return mapped
    return "SUM"


def _field_caption_for_column(
    column_attr: str | None,
    instance_name: str | None,
    name_map: dict[str, str],
) -> str:
    key = _column_name_key(column_attr)
    if key:
        caption = name_map.get(key) or name_map.get(f"[{key}]") or name_map.get(column_attr or "")
        if caption:
            return caption
        return key
    inner = _column_name_key(instance_name)
    if ":" in inner:
        parts = inner.split(":")
        # e.g. pcto:sum:Total Profit:qk
        if len(parts) >= 3:
            return parts[2] or inner
    return inner or "Field"


def _table_calc_addressing(table_calc: ET.Element) -> str:
    ordering = (table_calc.get("ordering-type") or table_calc.get("ordering") or "Rows").strip()
    return _ORDERING_LABELS.get(ordering, ordering or "Table (Across)")


def _table_calc_label(calc_type: str | None) -> str:
    if not calc_type:
        return "Table Calculation"
    return _TABLE_CALC_LABELS.get(calc_type, calc_type)


def _describe_table_calc(
    *,
    calc_type: str | None,
    derivation: str | None,
    instance_name: str | None,
    column_attr: str | None,
    name_map: dict[str, str],
    table_calc: ET.Element,
) -> tuple[str, str]:
    """Return (caption, formula) for a worksheet table calc pill."""

    label = _table_calc_label(calc_type)
    field_caption = _field_caption_for_column(column_attr, instance_name, name_map)
    agg = _agg_from_derivation(derivation, instance_name)
    addressing = _table_calc_addressing(table_calc)
    caption = f"{label} {field_caption}".strip()
    if agg:
        formula = f"{label} {agg}({field_caption}) along {addressing}"
    else:
        formula = f"{label} {field_caption} along {addressing}"
    return caption, formula


def _worksheet_datasource(ws: ET.Element) -> tuple[str | None, str | None]:
    """Return (datasource name, caption) from a worksheet element."""

    for child in ws.iter():
        if _local_tag(child.tag) == "datasource":
            name = child.get("name")
            if name and name.lower() != "parameters":
                return name, child.get("caption")
    return None, None


def _parse_table_calcs(
    root: ET.Element,
    name_maps: dict[str, dict[str, str]],
    datasources: list[DatasourceInfo],
) -> tuple[list[RawCalculatedField], list[ColumnField]]:
    """Collect worksheet ``<column-instance>/<table-calc>`` pills.

    These are not datasource ``<column><calculation>`` formulas. They are listed
    as calculated fields with a human-readable description; SQL transpile skips them.
    """

    ds_caption_by_name = {ds.name: ds.caption for ds in datasources}
    calculated: list[RawCalculatedField] = []
    fields: list[ColumnField] = []
    seen: set[tuple[str, str]] = set()

    for ws in root.iter():
        if _local_tag(ws.tag) != "worksheet":
            continue
        sheet_name = ws.get("name") or "sheet"
        ds_name, ds_caption_attr = _worksheet_datasource(ws)
        if not ds_name:
            # Fall back to first non-parameters datasource
            for ds in datasources:
                if (ds.name or "").lower() != "parameters":
                    ds_name = ds.name
                    break
        if not ds_name:
            ds_name = "datasource"
        ds_caption = ds_caption_attr or ds_caption_by_name.get(ds_name) or ds_name
        name_map = name_maps.get(ds_name, {})

        for el in ws.iter():
            if _local_tag(el.tag) != "column-instance":
                continue
            table_calc = None
            for child in list(el):
                if _local_tag(child.tag) == "table-calc":
                    table_calc = child
                    break
            if table_calc is None:
                continue
            calc_type = table_calc.get("type")
            if not calc_type or calc_type == "None":
                continue
            instance_name = el.get("name") or ""
            column_attr = el.get("column")
            dedupe_key = (ds_name, instance_name or f"{calc_type}:{column_attr}:{sheet_name}")
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            caption, formula = _describe_table_calc(
                calc_type=calc_type,
                derivation=el.get("derivation"),
                instance_name=instance_name,
                column_attr=column_attr,
                name_map=name_map,
                table_calc=table_calc,
            )
            raw_name = instance_name or f"[table-calc:{caption}]"
            calculated.append(
                RawCalculatedField(
                    name=raw_name,
                    caption=caption,
                    datatype=el.get("datatype") or "real",
                    role="measure",
                    formula=formula,
                    datasource_name=ds_name,
                    datasource_caption=ds_caption,
                    is_table_calc=True,
                    worksheet=sheet_name,
                    table_calc_type=calc_type,
                )
            )
            fields.append(
                ColumnField(
                    name=raw_name,
                    caption=caption,
                    datatype=el.get("datatype") or "real",
                    role="measure",
                    hidden=False,
                    calculated=True,
                    formula=formula,
                    datasource=ds_name,
                    datasource_caption=ds_caption,
                )
            )

    return calculated, fields


def _parse_worksheets(root: ET.Element) -> list[WorksheetInfo]:
    sheets: list[WorksheetInfo] = []
    for el in root.iter():
        if _local_tag(el.tag) != "worksheet":
            continue
        name = el.get("name") or "sheet"
        ds_name: str | None = None
        rows: list[str] = []
        cols: list[str] = []
        dimensions: list[str] = []
        used: list[str] = []
        mark_class: str | None = None

        def _add_used(field_name: str, is_dim: bool) -> None:
            if not field_name or is_mn_mv_token(field_name):
                return
            if field_name not in used:
                used.append(field_name)
            if is_dim and field_name not in dimensions:
                dimensions.append(field_name)

        for child in el.iter():
            tag = _local_tag(child.tag)
            if tag == "datasource" and ds_name is None:
                ds_name = child.get("name") or ds_name
            if tag == "mark" and mark_class is None:
                mark_class = child.get("class") or child.get("mark") or mark_class
            if tag in {"rows", "cols"}:
                bits = _fields_from_shelf_text(_text_content(child))
                target = rows if tag == "rows" else cols
                for field_name, is_dim in bits:
                    if is_mn_mv_token(field_name):
                        continue
                    if field_name not in target:
                        target.append(field_name)
                    _add_used(field_name, is_dim)
            if tag in {"encoding", "filter"}:
                raw = child.get("field") or child.get("column") or child.get("name")
                if raw:
                    bits = _fields_from_shelf_text(raw if "[" in raw else f"[{raw}]")
                    if not bits:
                        key = _column_name_key(raw)
                        bits = [(key, False)] if key else []
                    for field_name, _is_dim in bits:
                        _add_used(field_name, False)
        sheets.append(
            scrub_worksheet_mn_mv(
                WorksheetInfo(
                    name=name,
                    datasource=ds_name,
                    rows=rows,
                    cols=cols,
                    dimensions=dimensions,
                    used_fields=used,
                    mark_class=mark_class,
                )
            )
        )
    return sheets


def parse_workbook(source: Source) -> ParsedWorkbook:
    data, filename = _read_bytes(source)
    if not data:
        raise WorkbookParseError("Workbook is empty")
    archive_members: list[ArchiveMember] = []
    if _is_zip(data, filename):
        data, archive_members = _twb_from_zip(data)
        if filename and filename.lower().endswith(".twbx"):
            filename = Path(filename).stem + ".twb"
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise WorkbookParseError(f"Invalid Tableau XML: {exc}") from exc

    workbook_name = root.get("source-build")
    repo = None
    for el in root.iter():
        if _local_tag(el.tag) == "repository-location":
            repo = el.get("id")
            break

    datasources: list[DatasourceInfo] = []
    calculated: list[RawCalculatedField] = []
    name_maps: dict[str, dict[str, str]] = {}
    parameters: list[ParameterInfo] = []
    all_fields: list[ColumnField] = []

    for ds_el in _iter_direct_datasources(root):
        info, fields, name_map, params, columns = _parse_datasource(ds_el)
        datasources.append(info)
        name_maps[info.name] = name_map
        calculated.extend(fields)
        parameters.extend(params)
        all_fields.extend(columns)

    table_calcs, table_calc_fields = _parse_table_calcs(root, name_maps, datasources)
    calculated.extend(table_calcs)
    all_fields.extend(table_calc_fields)

    # Never treat Measure Names / Values layout tokens as calculated columns.
    calculated = [c for c in calculated if not is_mn_mv_token(c.name) and not is_mn_mv_token(c.caption)]
    all_fields = [f for f in all_fields if not is_mn_mv_token(f.name) and not is_mn_mv_token(f.caption)]

    measure_names = detect_measure_names_usages(
        root, name_maps=name_maps, datasources=datasources
    )

    return ParsedWorkbook(
        workbook_name=repo or workbook_name or filename,
        datasources=datasources,
        calculated_fields=calculated,
        name_maps=name_maps,
        archive_members=archive_members,
        parameters=parameters,
        dashboards=_parse_dashboards(root),
        fields=all_fields,
        worksheets=_parse_worksheets(root),
        measure_names=measure_names,
        joins=_parse_joins(root, datasources),
    )


def _parse_joins(root: ET.Element, datasources: list[DatasourceInfo]) -> list[JoinInfo]:
    """Extract Tableau join clauses into JoinInfo rows.

    Tableau stores joins as ``<relation type='join'>`` (with nested ``<clause>``)
    and sometimes as a ``<join>`` element.
    """

    ds_by_rel: dict[str, str] = {}
    for ds in datasources:
        caption = ds.caption or ds.name
        ds_by_rel[ds.name] = caption
        if ds.caption:
            ds_by_rel[ds.caption] = caption
        for rel in ds.relations:
            if rel.name:
                ds_by_rel[rel.name] = caption
            if rel.table:
                ds_by_rel[rel.table] = caption
                bare = rel.table.strip("[]")
                ds_by_rel[bare] = caption

    joins: list[JoinInfo] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _consume_join_el(el: ET.Element, join_type: str | None) -> None:
        clauses = [c for c in el.iter() if _local_tag(c.tag) == "clause"]
        for clause in clauses:
            fields = [
                e.get("op")
                for e in clause.iter()
                if _local_tag(e.tag) == "expression" and (e.get("op") or "").startswith("[")
            ]
            if len(fields) < 2:
                continue
            left_raw, right_raw = fields[0], fields[1]
            left_ds, left_col = _split_join_field(left_raw, ds_by_rel)
            right_ds, right_col = _split_join_field(right_raw, ds_by_rel)
            if not left_col or not right_col:
                continue
            key = (left_ds, right_ds, left_col, right_col)
            if key in seen:
                continue
            seen.add(key)
            joins.append(
                JoinInfo(
                    left_datasource=left_ds,
                    right_datasource=right_ds,
                    left_column=left_col,
                    right_column=right_col,
                    join_type=join_type,
                )
            )

    for el in root.iter():
        tag = _local_tag(el.tag)
        if tag == "join":
            _consume_join_el(el, el.get("type") or el.get("join"))
        elif tag == "relation" and (el.get("type") or "").lower() == "join":
            _consume_join_el(el, el.get("join") or el.get("type"))
    return joins


def _split_join_field(raw: str | None, ds_by_rel: dict[str, str]) -> tuple[str, str]:
    """Parse ``[Table].[Column]`` into (datasource_or_table, column)."""

    text = (raw or "").strip()
    parts = re.findall(r"\[([^\]]+)\]", text)
    if len(parts) >= 2:
        table, column = parts[0], parts[1]
        ds = ds_by_rel.get(table) or ds_by_rel.get(f"[{table}]") or table
        return ds, column
    if len(parts) == 1:
        return "Table", parts[0]
    return "Table", text.strip("[]")
