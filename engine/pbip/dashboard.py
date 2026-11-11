"""Tableau dashboard -> one Power BI page, with sheets at their zone positions.

Zone geometry in a ``.twb`` is expressed in 1/100000ths of the dashboard, so every
zone maps linearly onto the page. Patterns follow Franssens' render-verified
Superstore example: filled rectangles are ``shape`` visuals and text is a
``textbox`` overlay (a ``shape``'s own text does not render in Desktop).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from engine.pbip.status import DashboardOutcome, ElementStatus, FULL, NONE, PARTIAL, WorksheetOutcome
from engine.schema import DashboardInfo, DashboardZone, TranspileResult, WorksheetInfo

PAGE_W = 1920
PAGE_H = 1080
_UNITS = 100_000
_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.9.0/schema.json"
)
_PAGE_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/page/2.1.0/schema.json"
)
_SAFE = re.compile(r"[^a-zA-Z0-9]+")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_BUTTON = re.compile(r"\bbutton\b", re.IGNORECASE)
_FONT_SCALE = 1.25  # Tableau dashboards are authored ~1000px wide; the page is 1920


@dataclass
class DashboardPage:
    page_id: str
    files: dict[str, str] = field(default_factory=dict)
    worksheets: set[str] = field(default_factory=set)


def _lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def _flatten(zones: list[DashboardZone]) -> list[DashboardZone]:
    out: list[DashboardZone] = []
    for z in zones:
        out.append(z)
        out.extend(_flatten(z.children))
    return out


def _rect(z: DashboardZone) -> tuple[float, float, float, float]:
    x = (z.x or 0) / _UNITS * PAGE_W
    y = (z.y or 0) / _UNITS * PAGE_H
    w = (z.w or 0) / _UNITS * PAGE_W
    h = (z.h or 0) / _UNITS * PAGE_H
    return round(x, 1), round(y, 1), round(w, 1), round(h, 1)


def _container(visual_id: str, rect: tuple[float, float, float, float], z: int, visual: dict) -> dict:
    x, y, w, h = rect
    return {
        "$schema": _SCHEMA,
        "name": visual_id,
        "position": {"x": x, "y": y, "z": z, "width": w, "height": h, "tabOrder": z},
        "visual": visual,
    }


def _no_border() -> dict:
    return {"border": [{"properties": {"show": _lit("false")}}]}


def _shape(visual_id: str, z: DashboardZone, rect: tuple[float, float, float, float], order: int) -> dict:
    fill = z.fill if z.fill and _HEX.match(z.fill) else "#FFFFFF"
    visual = {
        "visualType": "shape",
        "drillFilterOtherVisuals": True,
        "objects": {
            "shape": [{"properties": {"tileShape": _lit("'rectangle'")}}],
            "fill": [
                {
                    "properties": {
                        "fillColor": {"solid": {"color": _lit(f"'{fill}'")}},
                        "transparency": _lit("0D"),
                    },
                    "selector": {"id": "default"},
                }
            ],
            "outline": [{"properties": {"show": _lit("false")}, "selector": {"id": "default"}}],
        },
        "visualContainerObjects": {
            "background": [{"properties": {"show": _lit("false")}}],
            **_no_border(),
        },
    }
    return _container(visual_id, rect, order, visual)


def _font_pt(size: str | None) -> str:
    try:
        return f"{max(8, round(float(size) * _FONT_SCALE))}pt"
    except (TypeError, ValueError):
        return "12pt"


def _textbox(visual_id: str, z: DashboardZone, rect: tuple[float, float, float, float], order: int) -> dict:
    color = z.font_color if z.font_color and _HEX.match(z.font_color) else "#252423"
    paragraphs = [
        {
            "horizontalTextAlignment": "left",
            "textRuns": [
                {
                    "value": line,
                    "textStyle": {
                        "fontFamily": "Segoe UI",
                        "fontSize": _font_pt(z.font_size),
                        "color": color,
                    },
                }
            ],
        }
        for line in (z.text or "").split("\n")
    ]
    visual = {
        "visualType": "textbox",
        "drillFilterOtherVisuals": True,
        "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
        "visualContainerObjects": _no_border(),
    }
    return _container(visual_id, rect, order, visual)


def build_dashboard_page(
    dash: DashboardInfo,
    ws_by_name: dict[str, WorksheetInfo],
    result: TranspileResult,
    table_name_by_ds: dict[str, str] | None,
    *,
    base: str,
    outcomes: list[WorksheetOutcome] | None = None,
    dashboard_outcomes: list[DashboardOutcome] | None = None,
) -> DashboardPage | None:
    """Emit a page for ``dash``; ``None`` when it holds nothing we can place."""

    from engine.pbip.pbir import _page_id, _visual_id, _worksheet_visual

    zones = [z for z in _flatten(dash.zones) if z.w and z.h]
    if not any(z.name in ws_by_name for z in zones):
        return None

    pid = _page_id(dash.name)
    page = DashboardPage(page_id=pid)
    page.files[f"{base}/pages/{pid}/page.json"] = (
        json.dumps(
            {
                "$schema": _PAGE_SCHEMA,
                "name": pid,
                "displayName": (dash.name or "Dashboard")[:100],
                "displayOption": "FitToPage",
                "height": PAGE_H,
                "width": PAGE_W,
            },
            indent=2,
        )
        + "\n"
    )

    rows: list[ElementStatus] = []
    slicer_fields: list[str] = []
    nav_rects: list[tuple[float, float, float, float]] = []
    sheet_visuals: dict[str, str] = {}
    order = 1000

    def emit(visual: dict) -> None:
        page.files[f"{base}/pages/{pid}/visuals/{visual['name']}/visual.json"] = (
            json.dumps(visual, indent=2, ensure_ascii=False) + "\n"
        )

    for zone in zones:
        rect = _rect(zone)
        label = zone.name or zone.type or "zone"
        kind = zone.type or ("worksheet" if zone.name in ws_by_name else "zone")
        order += 1000

        if zone.name in ws_by_name and zone.name not in page.worksheets:
            ws = ws_by_name[zone.name]
            visual, outcome = _worksheet_visual(
                ws,
                result,
                table_name_by_ds,
                page_name=pid,
                x=rect[0],
                y=rect[1],
                z=order,
                width=rect[2],
                height=rect[3],
            )
            outcome.page_label = dash.name
            page.worksheets.add(ws.name)
            if _BUTTON.search(ws.name):
                # Tableau navigation buttons -> one native pageNavigator over all of them
                nav_rects.append(rect)
                outcome.visual_type = "pageNavigator"
                outcome.visual_override = (
                    PARTIAL,
                    "Navigation button; replaced by a Power BI page navigator (styling differs)",
                )
                if outcomes is not None:
                    outcomes.append(outcome)
                continue
            if outcome.shelves_empty:
                outcome.visual_type = "(not placed)"
                outcome.visual_override = (
                    NONE,
                    "No field on this helper/text sheet could be mapped, so no visual was placed",
                )
                if outcomes is not None:
                    outcomes.append(outcome)
                rows.append(
                    ElementStatus(
                        "Sheet placement",
                        f"{ws.name} ({zone.x},{zone.y} {zone.w}x{zone.h})",
                        "-",
                        NONE,
                        "Helper/text sheet with no convertible fields; not placed",
                    )
                )
                continue
            if outcomes is not None:
                outcomes.append(outcome)
            emit(visual)
            sheet_visuals[ws.name] = visual["name"]
            rows.append(
                ElementStatus(
                    "Sheet placement",
                    f"{ws.name} ({zone.x},{zone.y} {zone.w}x{zone.h})",
                    f"{outcome.visual_type} at ({rect[0]:g}, {rect[1]:g}) {rect[2]:g}x{rect[3]:g}px",
                    FULL,
                    "Position and size scaled onto the 1920x1080 page",
                )
            )
        elif kind == "text" and (zone.fill or zone.text):
            visible_fill = bool(zone.fill and _HEX.match(zone.fill))
            if visible_fill:
                emit(_shape(_visual_id(), zone, rect, order))
            if zone.text:
                emit(_textbox(_visual_id(), zone, rect, order + 1))
                rows.append(
                    ElementStatus(
                        "Text",
                        (zone.text[:60] + "...") if len(zone.text) > 60 else zone.text,
                        "textbox" + (" over a filled shape" if visible_fill else ""),
                        PARTIAL,
                        "First run's font/color applied to the whole text; Tableau fonts replaced by Segoe UI",
                    )
                )
            else:
                rows.append(
                    ElementStatus(
                        "Background block",
                        f"{zone.fill} {zone.w}x{zone.h}",
                        "shape (rectangle)",
                        FULL,
                    )
                )
        elif kind == "filter":
            placed_slicer = _filter_slicer(zone, rect, order, result, table_name_by_ds)
            if placed_slicer is None:
                rows.append(
                    ElementStatus(
                        "Quick filter", label, "-", NONE,
                        "Filter field is not a column in the Power BI model; add a slicer manually",
                    )
                )
            else:
                slicer, caption = placed_slicer
                emit(slicer)
                slicer_fields.append(caption)
                rows.append(
                    ElementStatus(
                        "Quick filter", label, f"slicer [{caption}]", FULL,
                        "Dropdown slicer at the filter card's position",
                    )
                )
        elif kind == "paramctrl":
            param_slicer = _parameter_slicer(zone, rect, order, result)
            if param_slicer is None:
                rows.append(
                    ElementStatus(
                        "Parameter control", zone.param or label, "-", NONE,
                        "Only numeric and date parameters become what-if tables; add the slicer manually",
                    )
                )
            else:
                emit(param_slicer[0])
                rows.append(
                    ElementStatus(
                        "Parameter control", zone.param or label,
                        f"slicer on what-if table [{param_slicer[1]}]", FULL,
                        "Slicer on the generated GENERATESERIES parameter table",
                    )
                )
        elif kind == "title":
            title_zone = DashboardZone(
                type="text", name=zone.name, x=zone.x, y=zone.y, w=zone.w, h=zone.h,
                text=dash.name, font_size="18",
            )
            emit(_textbox(_visual_id(), title_zone, rect, order))
            rows.append(
                ElementStatus(
                    "Dashboard title", dash.name, "textbox", PARTIAL,
                    "Dashboard name placed as a text box; Tableau title formatting is not copied",
                )
            )
        elif kind in {"color", "size", "shape"}:
            rows.append(
                ElementStatus(
                    "Legend", f"{kind}: {label}", "visual legend", PARTIAL,
                    "Power BI shows each visual's own legend when it has a Legend field; "
                    "the Tableau legend card is not placed separately",
                )
            )
        elif kind == "bitmap":
            rows.append(ElementStatus("Image", label, "-", NONE, "Images are not embedded"))
        elif kind.startswith("layout") or kind in {"text", "zone"}:
            continue  # pure layout container, or an empty/unstyled spacer
        else:
            rows.append(ElementStatus("Dashboard object", f"{kind}: {label}", "-", NONE, "Not converted"))

    if nav_rects:
        left = min(r[0] for r in nav_rects)
        top = min(r[1] for r in nav_rects)
        right = max(r[0] + r[2] for r in nav_rects)
        bottom = max(r[1] + r[3] for r in nav_rects)
        emit(_page_navigator(_visual_id(), (left, top, right - left, bottom - top), order + 1000))
        rows.append(
            ElementStatus(
                "Navigation buttons",
                f"{len(nav_rects)} button sheets",
                "pageNavigator",
                PARTIAL,
                "Native page navigator lists every page; Tableau's active/inactive button styling is not copied",
            )
        )

    # Sheet filters without a fixed member list and without a filter card on the dashboard
    # get a slicer in a strip under the layout, so they stay adjustable in Power BI.
    from engine.pbip.pbir import sheet_slicers

    strip: list[tuple[dict, str, str]] = []
    for ws_name in sorted(page.worksheets):
        ws = ws_by_name[ws_name]
        if _BUTTON.search(ws.name):
            continue
        for slicer, caption in sheet_slicers(ws, result, table_name_by_ds, x=0, y=0, z=order):
            if caption in slicer_fields or any(c == caption for _, c, _ in strip):
                continue
            strip.append((slicer, caption, ws_name))
    if strip:
        strip = strip[:8]
        for i, (slicer, caption, ws_name) in enumerate(strip):
            slicer["position"].update(
                {"x": 20 + i * 250, "y": PAGE_H + 10, "width": 240, "height": 70, "z": order + 1 + i,
                 "tabOrder": order + 1 + i}
            )
            emit(slicer)
            slicer_fields.append(caption)
            rows.append(
                ElementStatus(
                    "Slicer", f"{caption} (filter on {ws_name})", f"slicer [{caption}]", PARTIAL,
                    "Filter without a fixed member list became a slicer under the dashboard layout",
                )
            )
        page_path = f"{base}/pages/{pid}/page.json"
        meta = json.loads(page.files[page_path])
        meta["height"] = PAGE_H + 90
        page.files[page_path] = json.dumps(meta, indent=2) + "\n"
    interactions, action_rows = dashboard_interactions(dash, sheet_visuals)
    rows.extend(action_rows)
    if interactions:
        page_path = f"{base}/pages/{pid}/page.json"
        meta = json.loads(page.files[page_path])
        meta["visualInteractions"] = interactions
        page.files[page_path] = json.dumps(meta, indent=2) + "\n"
    if slicer_fields and outcomes is not None:
        # A page-level slicer filters every sheet on the dashboard that shows its field.
        for o in outcomes:
            if o.page_name == pid:
                o.slicers.extend(c for c in slicer_fields if c not in o.slicers)
    if dashboard_outcomes is not None:
        dashboard_outcomes.append(DashboardOutcome(name=dash.name, page_name=pid, rows=rows))
    return page


def _in_csv(raw: str | None, name: str) -> bool:
    """True when ``name`` is one entry of the comma separated ``raw`` (names may hold commas)."""

    return bool(raw) and f",{name}," in f",{raw},"


def dashboard_interactions(
    dash: DashboardInfo,
    sheet_visuals: dict[str, str],
) -> tuple[list[dict], list[ElementStatus]]:
    """Dashboard actions -> PBIR ``visualInteractions`` plus one status row per action.

    A Tableau filter action filters its target sheets (``DataFilter``); a highlight action
    becomes ``HighlightFilter``. Sheets listed in the action's ``exclude`` parameter do not
    react (``NoFilter``). Only visuals that are placed on the page can be wired up.
    """

    rows: list[ElementStatus] = []
    # (source sheet, target sheet) -> kinds that reach it; sources -> targets they ignore
    reached: dict[tuple[str, str], set[str]] = {}
    triggers: set[str] = set()
    placed = list(sheet_visuals)
    for action in dash.actions:
        label = action.caption or action.name or "Action"
        if action.kind == "other":
            rows.append(
                ElementStatus(
                    "Dashboard action", label, "-", NONE,
                    f"{(action.command or 'This action type').replace('tsc:', '')} actions "
                    "(URL, go-to-sheet, parameter) are not converted",
                )
            )
            continue
        if action.target and action.target.strip() != (dash.name or "").strip():
            rows.append(
                ElementStatus(
                    "Dashboard action", label, "-", NONE,
                    f"Targets another dashboard ({action.target}); Power BI interactions only "
                    "work between visuals on one page",
                )
            )
            continue
        sources = [s for s in (action.source_sheets or placed) if s in sheet_visuals]
        sources = [s for s in sources if s not in action.source_excluded]
        excluded = {t for t in placed if _in_csv(action.target_excluded, t)}
        if not sources:
            rows.append(
                ElementStatus(
                    "Dashboard action", label, "-", NONE,
                    "Source sheet is not a visual on this page",
                )
            )
            continue
        for src in sources:
            triggers.add(src)
            for tgt in placed:
                if tgt == src or tgt in excluded:
                    continue
                reached.setdefault((src, tgt), set()).add(action.kind)
        scoped = bool(excluded) or bool(action.source_sheets)
        verb = "filters" if action.kind == "filter" else "highlights"
        detail = f"{', '.join(sources)} {verb} " + (
            "all other visuals" if not excluded else f"all except {', '.join(sorted(excluded))}"
        )
        if action.all_fields:
            status, note = FULL, "Mapped to a page visual interaction"
        else:
            status, note = PARTIAL, (
                "Mapped to a page visual interaction; Tableau limited the action to selected "
                "fields, Power BI uses every shared field"
            )
        if scoped and status == FULL:
            note += " (specific targets)"
        rows.append(ElementStatus("Dashboard action", label, f"visualInteractions: {detail}", status, note))

    interactions: list[dict] = []
    for src in sorted(triggers):
        for tgt in placed:
            if tgt == src:
                continue
            kinds = reached.get((src, tgt))
            if not kinds:
                itype = "NoFilter"
            elif "filter" in kinds:
                itype = "DataFilter"
            else:
                itype = "HighlightFilter"
            interactions.append({"source": sheet_visuals[src], "target": sheet_visuals[tgt], "type": itype})
    return interactions, rows


_PARAM_REF = re.compile(r"\[([^\]]+)\]\.\[([^\]]+)\]")


def _filter_slicer(
    zone: DashboardZone,
    rect: tuple[float, float, float, float],
    order: int,
    result: TranspileResult,
    table_name_by_ds: dict[str, str] | None,
) -> tuple[dict, str] | None:
    """Slicer at a Tableau quick-filter card's position, bound to the filtered column."""

    from engine.pbip.pbir import (
        _entity_for_worksheet, _field_caption, _flat_name_map, _model_fields,
        _resolve_caption, slicer_json,
    )

    match = _PARAM_REF.search(zone.param or "")
    if not match:
        return None
    ds_name, field_token = match.groups()
    entity = _entity_for_worksheet(WorksheetInfo(name="", datasource=ds_name), result, table_name_by_ds)
    columns, measures = _model_fields(result, entity, table_name_by_ds)
    resolved = _resolve_caption(
        f"[{field_token}]", result.calculated_fields, columns, measures, _flat_name_map(result)
    )
    if resolved is None or resolved[1]:
        return None
    x, y, w, h = rect
    slicer = slicer_json(entity, resolved[0], x=x, y=y, z=order, width=w, height=max(h, 60))
    return slicer, resolved[0] or _field_caption(field_token)


def _parameter_slicer(
    zone: DashboardZone,
    rect: tuple[float, float, float, float],
    order: int,
    result: TranspileResult,
) -> tuple[dict, str] | None:
    """Slicer on the what-if table generated for a numeric/date Tableau parameter."""

    from engine.pbip.pbir import slicer_json

    match = _PARAM_REF.search(zone.param or "")
    if not match:
        return None
    wanted = match.group(2)
    for info in (result.inspect.parameters if result.inspect else []):
        if wanted not in {info.name, info.name.strip("[]"), info.caption}:
            continue
        if (info.datatype or "").lower() not in {
            "integer", "int", "real", "float", "double", "date", "datetime",
        }:
            return None
        caption = (info.caption or info.name or "Parameter").strip()
        x, y, w, h = rect
        return slicer_json(caption, caption, x=x, y=y, z=order, width=w, height=max(h, 60)), caption
    return None


def _page_navigator(visual_id: str, rect: tuple[float, float, float, float], order: int) -> dict:
    visual = {
        "visualType": "pageNavigator",
        "drillFilterOtherVisuals": True,
        "visualContainerObjects": {
            "background": [{"properties": {"show": _lit("false")}}],
            **_no_border(),
        },
    }
    return _container(visual_id, rect, order, visual)
