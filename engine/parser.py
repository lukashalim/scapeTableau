"""In-memory .twb / .twbx ingest using xml.etree and zipfile."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree as ET

from engine.redact import redact_connect_fragment, redact_password_attr
import re

from engine.schema import (
    ArchiveKind,
    ArchiveMember,
    ColumnField,
    ConnectionInfo,
    DashboardInfo,
    DashboardZone,
    DatasourceInfo,
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
            parameters.append(
                ParameterInfo(
                    name=col_name or internal,
                    caption=col_caption or internal,
                    datatype=el.get("datatype"),
                    value=el.get("value") or el.get("param-domain-type"),
                    formula=formula,
                    datasource=name,
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

        def _add_used(field_name: str, is_dim: bool) -> None:
            if not field_name:
                return
            if field_name not in used:
                used.append(field_name)
            if is_dim and field_name not in dimensions:
                dimensions.append(field_name)

        for child in el.iter():
            tag = _local_tag(child.tag)
            if tag == "datasource" and ds_name is None:
                ds_name = child.get("name") or ds_name
            if tag in {"rows", "cols"}:
                bits = _fields_from_shelf_text(_text_content(child))
                target = rows if tag == "rows" else cols
                for field_name, is_dim in bits:
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
            WorksheetInfo(
                name=name,
                datasource=ds_name,
                rows=rows,
                cols=cols,
                dimensions=dimensions,
                used_fields=used,
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
    )
