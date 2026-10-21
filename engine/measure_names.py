"""Tableau Measure Names / Measure Values detection and conversion notes.

Measure Names (``[:Measure Names]``) and Measure Values / Multiple Values are
worksheet layout tokens, not real columns. EscapeTableau must never emit them
as SQL view columns. Instead we:

* list the concrete measures from the Measure Names filter / shelves
* emit worksheet comments in the SQL script
* document a DAX strategy (a / b / c) and emit individual measures
* surface a short conversion summary on the transpile result
"""

from __future__ import annotations

import re
from typing import Literal
from xml.etree import ElementTree as ET

from engine.schema import (
    CalculatedField,
    ColumnField,
    DatasourceInfo,
    MeasureNamesUsage,
    WorksheetInfo,
)

Strategy = Literal["a", "b", "c"]

_MN_NAMES = {
    "measure names",
    ":measure names",
    "multiple values",
    "measure values",
    ":measure values",
}
_SHELF_FIELD = re.compile(r"\[([^\]]+)\]")
_INSTANCE = re.compile(
    r"^(?P<agg>[a-zA-Z]+):(?P<body>.+):(?P<role>[a-zA-Z]+)$"
)


def _local_tag(tag: str | None) -> str:
    if not tag:
        return ""
    return tag.split("}")[-1]


def _strip_brackets(name: str | None) -> str:
    text = (name or "").strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1]
    return text


def is_measure_names_token(name: str | None) -> bool:
    """True for Measure Names shelf / filter tokens."""

    key = _strip_brackets(name).strip().lower()
    if key.startswith(":"):
        key = key[1:].strip()
    return key in {"measure names"}


def is_measure_values_token(name: str | None) -> bool:
    """True for Measure Values / Multiple Values shelf tokens."""

    key = _strip_brackets(name).strip().lower()
    if key.startswith(":"):
        key = key[1:].strip()
    return key in {"measure values", "multiple values"}


def is_mn_mv_token(name: str | None) -> bool:
    return is_measure_names_token(name) or is_measure_values_token(name)


def filter_mn_mv_names(names: list[str]) -> list[str]:
    """Drop Measure Names / Values tokens from a field name list."""

    return [n for n in names if not is_mn_mv_token(n)]


def _caption_from_instance(inner: str, name_map: dict[str, str]) -> str | None:
    """Map ``sum:Sales:qk`` / ``usr:Calculation_…:qk`` to a friendly caption."""

    text = (inner or "").strip().strip('"').strip("'")
    # Nested "[ds].[sum:Sales:qk]" or escaped Tableau member strings
    if "].[" in text:
        text = text.split("].[", 1)[-1]
    text = text.strip().strip('"').strip("'").rstrip("]").lstrip("[")
    text = _strip_brackets(text)
    match = _INSTANCE.match(text)
    body = match.group("body") if match else text
    if is_mn_mv_token(body) or is_mn_mv_token(text):
        return None
    for key in (body, f"[{body}]", text, f"[{text}]"):
        caption = name_map.get(key)
        if caption and not is_mn_mv_token(caption):
            return caption
    # Fall back to bare field body (not the agg: prefix)
    if body and not is_mn_mv_token(body):
        return body
    return None


def _shelf_has_mn(text: str | None) -> bool:
    if not text:
        return False
    for match in _SHELF_FIELD.finditer(text):
        if is_measure_names_token(match.group(1)):
            return True
    return False


def _shelf_has_mv(text: str | None) -> bool:
    if not text:
        return False
    for match in _SHELF_FIELD.finditer(text):
        if is_measure_values_token(match.group(1)):
            return True
    return False


def _choose_strategy(*, has_mn: bool, has_mv: bool, shelves: list[str]) -> Strategy:
    """Classify worksheet Measure Names/Values layout.

    * **a** — Values well / measures expand as columns or text (default Power BI path)
    * **b** — Names well only (MN as a category axis, no Values / Multiple Values)
    * **c** — Names × Values matrix (both MN and Multiple Values on shelves)
    """

    if has_mn and has_mv:
        return "c"
    if has_mn and not has_mv:
        # MN on Columns/Rows without Multiple Values still expands measure values
        # into the mark (ProductDetailSheet) — treat as Values-well strategy a
        # unless MN is the only pill and no value channel (then b).
        if "Text" in shelves or "Columns" in shelves:
            return "a"
        return "b"
    if has_mv:
        return "a"
    return "a"


def _strategy_label(strategy: Strategy) -> str:
    return {
        "a": "Values well (Columns / Text)",
        "b": "Names well (Measure Names axis)",
        "c": "Names × Values matrix",
    }[strategy]


def _collect_filter_measures(ws: ET.Element, name_map: dict[str, str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for el in ws.iter():
        if _local_tag(el.tag) != "groupfilter":
            continue
        if (el.get("function") or "").lower() != "member":
            continue
        level = el.get("level") or ""
        if not is_measure_names_token(level) and "measure names" not in level.lower():
            continue
        member = el.get("member") or ""
        caption = _caption_from_instance(member, name_map)
        if not caption or caption in seen:
            continue
        seen.add(caption)
        found.append(caption)
    return found


def _encoding_shelves(ws: ET.Element) -> list[str]:
    shelves: list[str] = []
    for el in ws.iter():
        if _local_tag(el.tag) != "encoding":
            continue
        # <text column="..."/> under encodings, or encoding attr
        parent = None
        # column attr on text/encoding children handled below
        pass
    for el in ws.iter():
        tag = _local_tag(el.tag)
        if tag == "text" and el.get("column"):
            if "Text" not in shelves:
                shelves.append("Text")
        if tag == "filter" and "Measure Names" in (el.get("column") or ""):
            if "Filters" not in shelves:
                shelves.append("Filters")
    return shelves


def detect_measure_names_usages(
    root: ET.Element,
    *,
    name_maps: dict[str, dict[str, str]],
    datasources: list[DatasourceInfo],
) -> list[MeasureNamesUsage]:
    """Walk worksheets and return Measure Names / Values usages."""

    ds_caption = {ds.name: (ds.caption or ds.name) for ds in datasources}
    usages: list[MeasureNamesUsage] = []

    for ws in root.iter():
        if _local_tag(ws.tag) != "worksheet":
            continue
        sheet = ws.get("name") or "sheet"
        ds_name: str | None = None
        for child in ws.iter():
            if _local_tag(child.tag) == "datasource":
                name = child.get("name")
                if name and name.lower() != "parameters":
                    ds_name = name
                    break
        name_map = name_maps.get(ds_name or "", {})
        # Merge all name maps as fallback for caption resolution
        if not name_map and name_maps:
            merged: dict[str, str] = {}
            for m in name_maps.values():
                merged.update(m)
            name_map = merged

        rows_text = ""
        cols_text = ""
        for child in ws.iter():
            tag = _local_tag(child.tag)
            if tag == "rows" and not rows_text:
                rows_text = "".join(child.itertext())
            if tag == "cols" and not cols_text:
                cols_text = "".join(child.itertext())

        has_mn = _shelf_has_mn(rows_text) or _shelf_has_mn(cols_text)
        has_mv = _shelf_has_mv(rows_text) or _shelf_has_mv(cols_text)
        for child in ws.iter():
            if _local_tag(child.tag) == "filter":
                col = child.get("column") or ""
                if is_measure_names_token(col.split("].[")[-1] if "].[" in col else col):
                    has_mn = True
            if _local_tag(child.tag) == "text" and child.get("column"):
                token = child.get("column") or ""
                if is_measure_values_token(token.split("].[")[-1] if "].[" in token else token):
                    has_mv = True
                if is_measure_names_token(token.split("].[")[-1] if "].[" in token else token):
                    has_mn = True

        if not has_mn and not has_mv:
            continue

        shelves: list[str] = []
        if _shelf_has_mn(cols_text) or _shelf_has_mv(cols_text):
            shelves.append("Columns")
        if _shelf_has_mn(rows_text) or _shelf_has_mv(rows_text):
            shelves.append("Rows")
        for shelf in _encoding_shelves(ws):
            if shelf == "Filters":
                continue
            if shelf not in shelves:
                shelves.append(shelf)
        if not shelves:
            # MN only appears as a filter (no shelf pill)
            shelves = ["Filters"] if has_mn else ["Columns"]

        measures = _collect_filter_measures(ws, name_map)
        strategy = _choose_strategy(has_mn=has_mn, has_mv=has_mv, shelves=shelves)
        usages.append(
            MeasureNamesUsage(
                worksheet=sheet,
                datasource=ds_caption.get(ds_name or "", ds_name),
                measures=measures,
                shelves=shelves,
                strategy=strategy,
                has_measure_names=has_mn,
                has_measure_values=has_mv,
            )
        )
    return usages


def sql_measure_names_comments(usages: list[MeasureNamesUsage]) -> list[str]:
    """SQL script comment lines listing measures per worksheet."""

    if not usages:
        return []
    lines = ["-- Measure Names / Values (layout tokens — not emitted as SQL columns)", ""]
    for usage in usages:
        measures = ", ".join(f"[{m}]" for m in usage.measures) or "(no filter members)"
        shelf = " / ".join(usage.shelves) if usage.shelves else "shelves"
        lines.append(
            f"-- Worksheet '{usage.worksheet}' uses Measure Names/Values: "
            f"{measures} (on {shelf})"
        )
    lines.append("")
    return lines


def dax_measure_names_block(
    usages: list[MeasureNamesUsage],
    *,
    dax_table: str,
    calculated_fields: list[CalculatedField] | None = None,
    columns: list[ColumnField] | None = None,
) -> list[str]:
    """DAX strategy comments plus SUM stubs for raw measure columns."""

    if not usages:
        return []
    calc_captions = {f.caption for f in (calculated_fields or [])}
    column_by_caption = {
        c.caption: c for c in (columns or []) if c.caption and not c.calculated
    }
    lines = ["", "// Measure Names / Values strategies", ""]
    emitted_raw: set[str] = set()
    for usage in usages:
        label = _strategy_label(usage.strategy)
        lines.append(
            f"// Worksheet '{usage.worksheet}' strategy {usage.strategy} — {label}"
        )
        if not usage.measures:
            lines.append("//   (no Measure Names filter members)")
            lines.append("")
            continue
        for measure in usage.measures:
            lines.append(f"//   [{measure}]  →  SUM({dax_table}[{measure}])")
            # Emit SUM stubs for raw (non-calculated) columns not already measures
            if measure in calc_captions or measure in emitted_raw:
                continue
            col = column_by_caption.get(measure)
            if col is None:
                # Still emit a stub when the filter lists a known field caption
                emitted_raw.add(measure)
                continue
            emitted_raw.add(measure)
        lines.append("")
    return lines


def dax_raw_measure_assignments(
    usages: list[MeasureNamesUsage],
    *,
    dax_table: str,
    calculated_fields: list[CalculatedField] | None = None,
) -> list[str]:
    """``[Sales] := SUM(Extract[Sales])`` lines for raw MN filter members."""

    calc_captions = {f.caption for f in (calculated_fields or [])}
    lines: list[str] = []
    seen: set[str] = set()
    for usage in usages:
        if usage.strategy not in {"a", "c"}:
            # strategy b still benefits from individual measures for PBI
            pass
        for measure in usage.measures:
            if measure in calc_captions or measure in seen:
                continue
            seen.add(measure)
            lines.append(f"[{measure}] := SUM({dax_table}[{measure}])")
    return lines


def build_conversion_summary(usages: list[MeasureNamesUsage]) -> str:
    """Human-readable Measure Names / Values conversion summary."""

    lines = [
        "# Measure Names / Values conversion",
        "",
        "Tableau Measure Names / Measure Values are worksheet layout tokens. "
        "They are never emitted as SQL columns. Concrete measures from the "
        "Measure Names filter become individual SQL/DAX measures.",
        "",
        "Strategies: **a** Values well · **b** Names well · **c** Names × Values matrix",
        "",
    ]
    if not usages:
        lines.append("No worksheets use Measure Names / Values.")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Worksheet | Strategy | Shelves | Measures |")
    lines.append("| --- | --- | --- | --- |")
    for usage in usages:
        measures = ", ".join(usage.measures) if usage.measures else "—"
        shelves = ", ".join(usage.shelves) if usage.shelves else "—"
        lines.append(
            f"| {usage.worksheet} | {usage.strategy} ({_strategy_label(usage.strategy)}) "
            f"| {shelves} | {measures} |"
        )
    lines.append("")
    return "\n".join(lines)


def scrub_worksheet_mn_mv(sheet: WorksheetInfo) -> WorksheetInfo:
    """Remove MN/MV tokens from worksheet field lists (mutates a copy conceptually)."""

    sheet.rows = filter_mn_mv_names(sheet.rows)
    sheet.cols = filter_mn_mv_names(sheet.cols)
    sheet.dimensions = filter_mn_mv_names(sheet.dimensions)
    sheet.used_fields = filter_mn_mv_names(sheet.used_fields)
    return sheet


def reject_mn_mv_calculated(fields: list[CalculatedField]) -> list[CalculatedField]:
    """Drop any calculated fields that are Measure Names / Values tokens."""

    return [
        f
        for f in fields
        if not is_mn_mv_token(f.name) and not is_mn_mv_token(f.caption)
    ]
