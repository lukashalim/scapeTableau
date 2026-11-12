"""CONVERSION_STATUS.md / .json: per-worksheet, per-element conversion status.

Each worksheet is broken into the elements Tableau authors care about (visual type,
fields on shelves, location/detail, filters, color/size/label/tooltip encodings,
sorting, calculated fields, formatting) and each is marked

* **Full**          - reproduced in Power BI as authored
* **Partial**       - something equivalent exists but behaves or looks different
* **Not converted** - dropped; needs manual work in Power BI
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from engine.schema import CalculatedField, ParseStatus, TranspileResult, WorksheetInfo

FULL = "Full"
PARTIAL = "Partial"
NONE = "Not converted"

_ICON = {FULL: "✅", PARTIAL: "🟡", NONE: "❌"}


@dataclass
class WorksheetOutcome:
    """What ``build_pbir_files`` actually emitted for one worksheet."""

    name: str
    page_name: str
    mark: str
    visual_type: str
    entity: str = ""
    dims: list[str] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    agg_columns: dict[str, int] = field(default_factory=dict)
    location: str | None = None
    applied_filters: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (raw field, reason)
    added_fields: list[str] = field(default_factory=list)  # fields we added that Tableau lacked
    page_label: str = ""  # dashboard name when the sheet lives on a dashboard page
    # Set when the sheet was deliberately not emitted as a data visual: (status, note)
    visual_override: tuple[str, str] | None = None
    series: str | None = None  # color dimension bound to the legend/Series well
    size_measure: str | None = None  # measure bound to the scatter Size well
    shelves_empty: bool = False  # nothing resolved; model fields were substituted
    visual_fallback: bool = False  # chosen visual had no data bound -> plain table
    # "<kind>:<field>" -> (status, Power BI target, note) for encodings we actually applied
    applied_encodings: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    # field -> note, for sorts written to the visual's sortDefinition
    applied_sorts: dict[str, str] = field(default_factory=dict)
    # Formatting pieces carried over (title, background, mark colour, labels ...)
    formatting_applied: list[str] = field(default_factory=list)
    # categorical filters turned into slicers on the page
    slicers: list[str] = field(default_factory=list)


@dataclass
class ElementStatus:
    element: str
    tableau: str
    powerbi: str
    status: str
    note: str = ""


@dataclass
class DashboardOutcome:
    """What ``build_dashboard_page`` emitted for one Tableau dashboard."""

    name: str
    page_name: str
    rows: list[ElementStatus] = field(default_factory=list)


@dataclass
class WorksheetStatus:
    worksheet: str
    page: str
    visual_type: str
    elements: list[ElementStatus] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for e in self.elements if e.status == status)


# --------------------------------------------------------------------------- helpers


def _display(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    parts = text.split(":")
    if len(parts) >= 3:
        return parts[1]
    return text


def _nm(raw: str, lookup: dict[str, CalculatedField]) -> str:
    """Display name with Tableau's internal ``Calculation_123`` ids resolved to captions."""
    name = _display(raw)
    f = lookup.get(name)
    if f:
        return f.caption
    return getattr(lookup, "captions", {}).get(name, name)


def _is_generated_geo(raw: str) -> bool:
    low = (raw or "").lower()
    return "(generated)" in low and ("latitude" in low or "longitude" in low)


def _join(items: list[str]) -> str:
    return ", ".join(items) if items else "-"


def _bound_names(outcome: WorksheetOutcome) -> set[str]:
    names = set(outcome.dims) | set(outcome.measures) | set(outcome.agg_columns)
    if outcome.location:
        names.add(outcome.location)
    return names


class _Lookup(dict):
    """Calculated fields by caption/name, plus Tableau internal column name -> caption."""

    captions: dict[str, str]


def _calc_lookup(result: TranspileResult) -> dict[str, CalculatedField]:
    lookup = _Lookup()
    lookup.captions = {}
    for per_ds in (getattr(result, "name_maps", None) or {}).values():
        for internal, caption in per_ds.items():
            lookup.captions.setdefault(internal.strip("[]"), caption)
    for f in result.calculated_fields:
        for key in (f.caption, (f.name or "").strip("[]")):
            if key:
                lookup.setdefault(key, f)
    return lookup


def _calc_status(f: CalculatedField) -> tuple[str, str]:
    # Table calcs are skipped for SQL but still emitted as DAX measures: judge the DAX.
    if f.dax and f.dax_status == ParseStatus.ok and f.status == ParseStatus.skipped:
        return PARTIAL, "Table calculation emitted as a DAX measure; addressing is approximated"
    if f.status == ParseStatus.skipped or f.dax_status == ParseStatus.skipped:
        return NONE, f.error or "Skipped by the translator"
    if f.status != ParseStatus.ok or f.dax_status not in (None, ParseStatus.ok):
        return NONE, f.error or "Translation failed"
    dax = f.dax or ""
    if "ALLSELECTED" in dax or "RANKX" in dax:
        return PARTIAL, "Table calculation approximated with window DAX"
    if "ALLEXCEPT" in dax or "ALL(" in dax:
        return PARTIAL, "FIXED LOD became CALCULATE; slicer interaction differs"
    return FULL, "Measure" if f.dax_kind == "measure" else "Calculated column"


# Tableau mark -> {Power BI visual: (status, caveat)}
_MARK_MAPPING: dict[str, dict[str, tuple[str, str]]] = {
    "bar": {
        "clusteredBarChart": (FULL, ""),
        "clusteredColumnChart": (FULL, ""),
        "hundredPercentStackedColumnChart": (PARTIAL, "Stacked bars approximated as 100% stacked columns"),
    },
    "automatic": {
        "clusteredBarChart": (PARTIAL, "Tableau 'Automatic' resolved from shelf contents"),
        "clusteredColumnChart": (PARTIAL, "Tableau 'Automatic' resolved from shelf contents"),
        "lineChart": (FULL, "Date on the axis: Tableau draws a line"),
        "card": (PARTIAL, "Tableau 'Automatic' resolved from shelf contents"),
        "multiRowCard": (PARTIAL, "Tableau 'Automatic' resolved from shelf contents"),
        "tableEx": (PARTIAL, "Tableau 'Automatic' resolved from shelf contents"),
        "map": (PARTIAL, "Symbol map inferred from generated latitude and longitude"),
        "filledMap": (PARTIAL, "Filled map inferred from a geometry encoding"),
        "shapeMap": (PARTIAL, "State polygons drawn with the USA shape map"),
    },
    "pie": {"pieChart": (FULL, "")},
    "text": {"tableEx": (FULL, ""), "card": (FULL, ""), "multiRowCard": (FULL, "")},
    "multipolygon": {"filledMap": (FULL, ""), "shapeMap": (FULL, "")},
    "polygon": {"filledMap": (FULL, ""), "shapeMap": (FULL, "")},
    "map": {
        "map": (PARTIAL, "Symbol map; marks/size not carried over"),
        "filledMap": (PARTIAL, "Symbol map shown as filled map"),
    },
    "line": {"lineChart": (FULL, "")},
    "area": {"areaChart": (FULL, "")},
    "circle": {
        "scatterChart": (FULL, "Columns feed X, Rows feed Y"),
        "card": (PARTIAL, "Single-measure circle marks collapsed to a card"),
    },
    "square": {
        "pivotTable": (PARTIAL, "Highlight table emitted as a matrix with a background colour scale"),
        "tableEx": (PARTIAL, "Square marks shown as a flat table"),
    },
    "ganttbar": {
        "clusteredBarChart": (PARTIAL, "Gantt bars shown as plain bars"),
        "clusteredColumnChart": (PARTIAL, "Gantt bars shown as plain bars"),
        "hundredPercentStackedColumnChart": (PARTIAL, "Gantt bars shown as plain bars"),
    },
}


def _visual_row(ws: WorksheetInfo, o: WorksheetOutcome) -> ElementStatus:
    mark = (o.mark or "automatic").lower()
    tableau = f"{ws.mark_class or 'Automatic'} mark"
    powerbi = o.visual_type
    if o.visual_override:
        status, note = o.visual_override
        return ElementStatus("Visual type", tableau, powerbi, status, note)
    if o.visual_fallback or o.shelves_empty:
        why = (
            "No fields could be mapped from the worksheet; a table of model fields was shown."
            if o.shelves_empty
            else "Chosen visual had nothing to bind; fell back to a table."
        )
        return ElementStatus("Visual type", tableau, powerbi, NONE, why)
    expected = _MARK_MAPPING.get(mark)
    if expected is None:
        return ElementStatus(
            "Visual type", tableau, powerbi, PARTIAL,
            f"No dedicated mapping for '{ws.mark_class}'; shown as {powerbi}",
        )
    if powerbi not in expected:
        return ElementStatus(
            "Visual type", tableau, powerbi, NONE,
            f"'{ws.mark_class}' should become {_join(sorted(expected))} but a {powerbi} was emitted",
        )
    status, note = expected[powerbi]
    return ElementStatus("Visual type", tableau, powerbi, status, note)


def _shelf_rows(
    ws: WorksheetInfo,
    o: WorksheetOutcome,
    datasource_names: set[str],
    lookup: dict[str, CalculatedField],
) -> list[ElementStatus]:
    rows: list[ElementStatus] = []
    bound = _bound_names(o)
    dropped = dict(o.dropped)
    for shelf, items in (("Rows", ws.rows), ("Columns", ws.cols)):
        for raw in items:
            name = _display(raw)
            if name in datasource_names:
                continue  # Tableau's datasource placeholder on a shelf, not a field
            name = _nm(raw, lookup)
            tableau = f"{shelf}: {name}"
            if _is_generated_geo(raw):
                if o.visual_type in {"filledMap", "map", "shapeMap"}:
                    rows.append(ElementStatus(
                        "Shelf field", tableau, f"{o.visual_type} geocoding", FULL,
                        "Tableau's generated lat/long replaced by Location geocoding",
                    ))
                else:
                    rows.append(ElementStatus(
                        "Shelf field", tableau, "-", NONE, "Generated coordinates have no Power BI field"
                    ))
                continue
            if name in o.agg_columns:
                rows.append(ElementStatus("Shelf field", tableau, f"Aggregated [{name}]", FULL))
            elif name in bound:
                rows.append(ElementStatus("Shelf field", tableau, f"[{name}]", FULL))
            else:
                reason = dropped.get(raw, "Field not found in the Power BI model")
                rows.append(ElementStatus(
                    "Shelf field", tableau, "-",
                    PARTIAL if reason.startswith("Constant helper") else NONE,
                    reason,
                ))
    return rows


def _location_row(
    ws: WorksheetInfo, o: WorksheetOutcome, lookup: dict[str, CalculatedField]
) -> list[ElementStatus]:
    out: list[ElementStatus] = []
    for raw in ws.detail_fields:
        name = _nm(raw, lookup)
        if o.location == name:
            out.append(ElementStatus("Detail / Location", f"Detail: {name}", f"Location [{name}]", FULL))
        elif name in _bound_names(o):
            out.append(ElementStatus("Detail / Location", f"Detail: {name}", f"[{name}]", PARTIAL,
                                     "On the visual but not as a Detail/Legend well"))
        else:
            out.append(ElementStatus("Detail / Location", f"Detail: {name}", "-", NONE, "Not bound"))
    return out


def _filter_rows(
    ws: WorksheetInfo, o: WorksheetOutcome, lookup: dict[str, CalculatedField]
) -> list[ElementStatus]:
    out: list[ElementStatus] = []
    for fld, members in ws.filters.items():
        name = _nm(fld, lookup)
        tableau = f"{name} = {_join(members)}"
        if name in o.applied_filters:
            out.append(ElementStatus("Filter", tableau, f"Visual filter [{name}] is {_join(members)}", FULL))
        elif name.startswith("Action ("):
            out.append(ElementStatus(
                "Filter", tableau, "Visual cross-filtering", PARTIAL,
                "Dashboard action filter state; Power BI cross-filters on click instead of a fixed value",
            ))
        else:
            out.append(ElementStatus("Filter", tableau, "-", NONE, "Field is not a column in the model"))
    for label in ws.unmapped_filters:
        if label.startswith("Action ("):
            # Power BI cross-filters between visuals on a page by default.
            out.append(ElementStatus(
                "Filter", label, "Visual cross-filtering", PARTIAL,
                "Dashboard action filter (click-to-filter); Power BI cross-filters visuals by "
                "default, but source/target sheets and fields are not mirrored exactly",
            ))
            continue
        if label.endswith("(categorical)"):
            fld = _nm(label[: -len(" (categorical)")], lookup)
            if fld in o.slicers:
                out.append(ElementStatus(
                    "Filter", label, f"Slicer [{fld}]", FULL,
                    "Filter without a fixed member list became a slicer",
                ))
                continue
        note = (
            "Filter has no fixed member list (all values / exclude / dynamic); "
            "Power BI shows all values"
            if label.endswith("(categorical)")
            else "Only fixed 'keep these values' filters are converted (not ranges, relative dates, context)"
        )
        out.append(ElementStatus("Filter", label, "-", NONE, note))
    return out


_ENCODING_LABEL = {
    "color": "Color",
    "size": "Size",
    "text": "Label",
    "tooltip": "Tooltip",
    "shape": "Shape",
    "path": "Path",
    "angle": "Angle",
    "wedge-size": "Wedge size",
}


def _encoding_rows(
    ws: WorksheetInfo, o: WorksheetOutcome, lookup: dict[str, CalculatedField]
) -> list[ElementStatus]:
    out: list[ElementStatus] = []
    bound = _bound_names(o)
    for kind, fields in ws.encodings.items():
        if kind == "lod":
            continue  # reported as Detail / Location
        label = _ENCODING_LABEL.get(kind, kind.title())
        for raw in fields:
            name = _nm(raw, lookup)
            applied = o.applied_encodings.get(f"{kind}:{name}")
            if applied is not None:
                status, target, note = applied
                out.append(ElementStatus(label, f"{label}: {name}", target, status, note))
                continue
            if kind == "color" and name == o.series:
                out.append(ElementStatus(label, f"{label}: {name}", f"Legend / Series [{name}]", FULL,
                                         "Color dimension drives the legend; palette not carried over"))
            elif kind == "size" and name == o.size_measure:
                out.append(ElementStatus(label, f"{label}: {name}", f"Size [{name}]", FULL))
            elif name in bound:
                out.append(ElementStatus(
                    label, f"{label}: {name}", f"[{name}] on the visual", PARTIAL,
                    f"Field is shown but the {label.lower()} encoding is not applied",
                ))
            else:
                out.append(ElementStatus(label, f"{label}: {name}", "-", NONE, "Encoding not converted"))
    if ws.custom_tooltip:
        out.append(ElementStatus(
            "Tooltip", "Custom tooltip text", "-", NONE,
            "Formatted tooltip text is not converted; Power BI default tooltips apply",
        ))
    return out


def _sort_rows(
    ws: WorksheetInfo, o: WorksheetOutcome, lookup: dict[str, CalculatedField]
) -> list[ElementStatus]:
    out: list[ElementStatus] = []
    for s in ws.sorts:
        name = _nm(s, lookup)
        note = o.applied_sorts.get(s) or o.applied_sorts.get(name)
        if note is not None:
            status = PARTIAL if "approximated" in note else FULL
            out.append(ElementStatus("Sort", f"Sort by {name}", "sortDefinition", status, note))
        else:
            out.append(ElementStatus("Sort", f"Sort by {name}", "-", NONE, "Sort order not converted"))
    return out


def _calc_rows(ws: WorksheetInfo, lookup: dict[str, CalculatedField]) -> list[ElementStatus]:
    seen: set[str] = set()
    names: list[str] = []
    pools = list(ws.rows) + list(ws.cols) + list(ws.used_fields) + list(ws.dimensions)
    pools += [n for fields in ws.encodings.values() for n in fields]
    pools += list(ws.filters)
    for raw in pools:
        name = _display(raw)
        if name in lookup and name not in seen:
            seen.add(name)
            names.append(name)
    out: list[ElementStatus] = []
    for name in names:
        f = lookup[name]
        status, note = _calc_status(f)
        out.append(ElementStatus("Calculated field", f"{f.caption}: {f.formula}"[:120], "DAX", status, note))
    return out


def _extras_rows(o: WorksheetOutcome) -> list[ElementStatus]:
    if not o.added_fields:
        return []
    return [ElementStatus(
        "Added by converter", "-", _join(o.added_fields), PARTIAL,
        "Columns added to the table that the Tableau sheet did not show",
    )]


def build_worksheet_statuses(
    result: TranspileResult,
    outcomes: list[WorksheetOutcome],
    dashboards: list[DashboardOutcome] | None = None,
) -> list[WorksheetStatus]:
    lookup = _calc_lookup(result)
    datasource_names = {n for d in result.datasources for n in (d.name, d.caption) if n}
    by_name = {o.name: o for o in outcomes}
    statuses: list[WorksheetStatus] = []
    for d in dashboards or []:
        statuses.append(WorksheetStatus(f"Dashboard: {d.name}", d.page_name, "page", list(d.rows)))
    for ws in result.worksheets:
        outcome = by_name.get(ws.name)
        if outcome is None:
            st = WorksheetStatus(ws.name, "-", "-")
            st.elements.append(ElementStatus(
                "Worksheet", ws.mark_class or "Automatic", "-", NONE,
                "Worksheet was not emitted as a Power BI page (page limit)",
            ))
            statuses.append(st)
            continue
        st = WorksheetStatus(
            ws.name,
            f"{outcome.page_label} (dashboard)" if outcome.page_label else outcome.page_name,
            outcome.visual_type,
        )
        st.elements.append(_visual_row(ws, outcome))
        if outcome.visual_override:
            statuses.append(st)  # helper / navigation sheet: only the verdict matters
            continue
        st.elements.extend(_shelf_rows(ws, outcome, datasource_names, lookup))
        st.elements.extend(_location_row(ws, outcome, lookup))
        st.elements.extend(_filter_rows(ws, outcome, lookup))
        st.elements.extend(_encoding_rows(ws, outcome, lookup))
        st.elements.extend(_sort_rows(ws, outcome, lookup))
        st.elements.extend(_calc_rows(ws, lookup))
        st.elements.extend(_extras_rows(outcome))
        if outcome.formatting_applied:
            st.elements.append(ElementStatus(
                "Formatting", "Title, colours, labels", _join(outcome.formatting_applied), PARTIAL,
                "Carried over; fonts, axes, mark styles and dark backgrounds are not converted "
                "(dark themes would make Power BI's default text unreadable)",
            ))
        else:
            st.elements.append(ElementStatus(
                "Formatting", "Colors, fonts, mark styles, axes, title", "Power BI defaults", NONE,
                "Visual formatting is not converted",
            ))
        _humanize_calc_ids(st, lookup)
        statuses.append(st)
    return statuses


_CALC_ID = re.compile(r"Calculation_\d+")


def _humanize_calc_ids(st: WorksheetStatus, lookup: dict[str, CalculatedField]) -> None:
    """Replace Tableau's internal ``Calculation_123`` ids with the field caption."""

    def swap(match: re.Match[str]) -> str:
        f = lookup.get(match.group(0))
        return f.caption if f else match.group(0)

    for e in st.elements:
        e.tableau = _CALC_ID.sub(swap, e.tableau)
        e.powerbi = _CALC_ID.sub(swap, e.powerbi)
        e.note = _CALC_ID.sub(swap, e.note)


# --------------------------------------------------------------------------- rendering


def _md(value: str) -> str:
    text = " ".join((value or "").split())  # collapses \r\n and runs of whitespace
    return text.replace("|", "\\|")


def render_status_markdown(statuses: list[WorksheetStatus]) -> str:
    total = {FULL: 0, PARTIAL: 0, NONE: 0}
    for s in statuses:
        for status in total:
            total[status] += s.count(status)
    lines = [
        "# Worksheet conversion status",
        "",
        "How each element of every Tableau worksheet was converted to Power BI.",
        "",
        f"- {_ICON[FULL]} **Full** - reproduced as authored",
        f"- {_ICON[PARTIAL]} **Partial** - an equivalent exists but behaves or looks different",
        f"- {_ICON[NONE]} **Not converted** - dropped; needs manual work in Power BI",
        "",
        f"**Totals:** {total[FULL]} full · {total[PARTIAL]} partial · {total[NONE]} not converted "
        f"across {len(statuses)} worksheets.",
        "",
        "## Summary",
        "",
        "| Worksheet | Power BI visual | Full | Partial | Not converted |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for s in statuses:
        lines.append(
            f"| {_md(s.worksheet)} | {_md(s.visual_type)} | {s.count(FULL)} | "
            f"{s.count(PARTIAL)} | {s.count(NONE)} |"
        )
    lines.append("")
    for s in statuses:
        lines += [
            f"## {_md(s.worksheet)}",
            "",
            f"Power BI page: `{s.page}` · visual: `{s.visual_type}`",
            "",
            "| Element | Tableau | Power BI | Status | Notes |",
            "| --- | --- | --- | --- | --- |",
        ]
        for e in s.elements:
            lines.append(
                f"| {_md(e.element)} | {_md(e.tableau)} | {_md(e.powerbi)} | "
                f"{_ICON[e.status]} {e.status} | {_md(e.note)} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_status_json(statuses: list[WorksheetStatus]) -> str:
    return json.dumps([asdict(s) for s in statuses], indent=2, ensure_ascii=False) + "\n"
