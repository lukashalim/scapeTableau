"""In-memory .twb / .twbx ingest using xml.etree and zipfile."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree as ET

from engine.schema import (
    ConnectionInfo,
    DatasourceInfo,
    ParsedWorkbook,
    RawCalculatedField,
    RelationInfo,
    WorkbookParseError,
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
    if filename and filename.lower().endswith(".twbx"):
        return True
    return False


def _twb_from_zip(data: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            twb_names = [
                info.filename
                for info in zf.infolist()
                if not info.is_dir() and info.filename.lower().endswith(".twb")
            ]
            if not twb_names:
                raise WorkbookParseError("Packaged workbook does not contain a .twb file")
            return zf.read(twb_names[0])
    except WorkbookParseError:
        raise
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


def _parse_datasource(ds: ET.Element) -> tuple[DatasourceInfo | None, list[RawCalculatedField], dict[str, str]]:
    name = ds.get("name") or "datasource"
    caption = ds.get("caption")
    is_parameters = name.lower() == "parameters"

    connections: list[ConnectionInfo] = []
    relations: list[RelationInfo] = []
    seen_conn: set[tuple[str | None, str | None, str | None, str | None]] = set()

    for el in ds.iter():
        tag = _local_tag(el.tag)
        if tag == "connection":
            info = ConnectionInfo(
                class_name=el.get("class"),
                dbname=el.get("dbname"),
                server=el.get("server"),
                filename=el.get("filename"),
            )
            key = (info.class_name, info.dbname, info.server, info.filename)
            if any(key) and key not in seen_conn:
                seen_conn.add(key)
                connections.append(info)
        elif tag == "relation":
            rel_type = el.get("type")
            custom_sql = None
            if rel_type == "text":
                custom_sql = _text_content(el).strip() or None
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
        if calc is None:
            continue
        formula = calc.get("formula") or ""
        calculated.append(
            RawCalculatedField(
                name=col_name or internal,
                caption=col_caption or internal,
                datatype=el.get("datatype"),
                role=el.get("role"),
                formula=formula,
                datasource_name=name,
                datasource_caption=caption,
            )
        )

    if is_parameters and not calculated:
        return None, [], {}

    info = DatasourceInfo(
        name=name,
        caption=caption,
        connections=connections,
        relations=relations,
    )
    return info, calculated, name_map


def parse_workbook(source: Source) -> ParsedWorkbook:
    data, filename = _read_bytes(source)
    if not data:
        raise WorkbookParseError("Workbook is empty")
    if _is_zip(data, filename):
        data = _twb_from_zip(data)
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

    for ds_el in _iter_direct_datasources(root):
        info, fields, name_map = _parse_datasource(ds_el)
        if info is not None:
            datasources.append(info)
            name_maps[info.name] = name_map
        calculated.extend(fields)

    return ParsedWorkbook(
        workbook_name=repo or workbook_name or filename,
        datasources=datasources,
        calculated_fields=calculated,
        name_maps=name_maps,
    )
