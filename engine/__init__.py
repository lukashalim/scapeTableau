"""EscapeTableau conversion engine: parse Tableau workbooks and emit ANSI SQL."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from engine.audit import assemble_result
from engine.extract import extract_to_xlsx
from engine.inspect import build_inspect
from engine.extract import build_inventory
from engine.parser import parse_workbook
from engine.schema import (
    CalculatedField,
    ParseStatus,
    TranspileResult,
    WorkbookParseError,
)
from engine.transpiler import resolve_internal_names, transpile_formula

__all__ = [
    "transpile",
    "transpile_formula",
    "extract_to_xlsx",
    "TranspileResult",
    "WorkbookParseError",
    "ParseStatus",
    "CalculatedField",
]


def transpile(
    source: str | Path | bytes | BinaryIO,
    *,
    dialect: str = "ansi",
) -> TranspileResult:
    """Parse a .twb/.twbx (path, bytes, or file object) and transpile calculations."""

    workbook = parse_workbook(source)
    fields: list[CalculatedField] = []
    for raw in workbook.calculated_fields:
        name_map = workbook.name_maps.get(raw.datasource_name, {})
        resolved = resolve_internal_names(raw.formula, name_map)
        sql, status, error = transpile_formula(resolved, dialect=dialect)
        fields.append(
            CalculatedField(
                name=raw.name,
                caption=raw.caption,
                datatype=raw.datatype,
                role=raw.role,
                formula=raw.formula,
                sql=sql,
                status=status,
                datasource=raw.datasource_caption or raw.datasource_name,
                error=error,
            )
        )
    inspect = build_inspect(workbook, fields)
    inventory = build_inventory(workbook=workbook)
    return assemble_result(
        fields,
        workbook.datasources,
        workbook_name=workbook.workbook_name,
        dialect=dialect,
        inspect=inspect,
        extract_inventory=inventory,
        dashboards=inspect.dashboards,
    )
