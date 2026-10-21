"""Stage 3: emit real PBIR definition/ pages + visuals from Tableau worksheets.

Visual JSON patterns follow vendored Franssens pbi.kb / tableEx templates under
``vendor/franssens-tableau-to-powerbi/templates/visuals/``. We do not depend on
pbir-utils — field binding is filled here.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from engine.pbip.render import render_json_template, render_template
from engine.schema import CalculatedField, ParseStatus, TranspileResult, WorksheetInfo

_VENDOR_VISUALS = (
    Path(__file__).resolve().parents[2]
    / "vendor"
    / "franssens-tableau-to-powerbi"
    / "templates"
    / "visuals"
)

_SAFE = re.compile(r"[^a-zA-Z0-9]+")


def _page_id(name: str) -> str:
    slug = _SAFE.sub("", (name or "page").lower())[:12] or "page"
    return (slug + uuid.uuid4().hex)[:20]


def _visual_id() -> str:
    return uuid.uuid4().hex[:20]


def _field_caption(raw: str) -> str:
    text = (raw or "").strip()
    # Tableau shelf tokens look like [none:Calculation_123:qk] or [Region]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1]
        parts = inner.split(":")
        if len(parts) >= 2 and parts[0] in {"none", "sum", "avg", "min", "max", "cnt", "ctd", "usr"}:
            # Prefer middle token; strip Aggregation suffixes later via caption map
            text = parts[1] if len(parts) > 1 else inner
        else:
            text = inner
    return text.strip("[]") or "Field"


def _resolve_caption(raw: str, fields: list[CalculatedField], ds_caption: str) -> tuple[str, bool]:
    """Return (caption, is_measure)."""

    key = _field_caption(raw)
    for f in fields:
        if f.caption == key or f.name.strip("[]") == key or key in {f.caption, f.name}:
            is_meas = (f.dax_kind == "measure") or (
                f.dax_status == ParseStatus.ok and "SUM(" in (f.dax or "").upper()
            )
            return f.caption, bool(is_meas)
    # Heuristic: sum:/avg: shelf → measure-like
    lowered = (raw or "").lower()
    is_meas = any(tok in lowered for tok in (":qk", "sum:", "avg:", "min:", "max:", "cnt:", "ctd:"))
    return key, is_meas


def _choose_visual_type(ws: WorksheetInfo, n_dims: int, n_meas: int) -> str:
    mark = (getattr(ws, "mark_class", None) or "").lower()
    if mark in {"pie"}:
        return "pieChart"
    if mark in {"circle"} and n_meas <= 1:
        return "card"
    if mark in {"bar", "ganttbar"}:
        return "hundredPercentStackedColumnChart" if n_dims >= 2 else "tableEx"
    if mark in {"line", "area"}:
        return "lineClusteredColumnComboChart" if n_meas >= 2 else "stackedAreaChart"
    if mark in {"square"} and n_dims >= 2:
        return "heatMap"
    if n_dims == 0 and n_meas >= 1:
        return "card" if n_meas == 1 else "multiRowCard"
    if n_dims >= 1 and n_meas >= 1:
        return "tableEx"
    return "tableEx"


def _projection(entity: str, prop: str, *, measure: bool) -> dict:
    kind = "Measure" if measure else "Column"
    return {
        "field": {
            kind: {
                "Expression": {"SourceRef": {"Entity": entity}},
                "Property": prop,
            }
        },
        "queryRef": f"{entity}.{prop}",
        "nativeQueryRef": prop,
        **({"active": True} if measure else {}),
    }


def _build_visual_json(
    *,
    visual_type: str,
    entity: str,
    dims: list[str],
    measures: list[str],
    x: int,
    y: int,
    z: int,
) -> dict:
    name = _visual_id()
    schema = (
        "https://developer.microsoft.com/json-schemas/fabric/item/report/"
        "definition/visualContainer/2.9.0/schema.json"
    )
    if visual_type == "card" and measures:
        query_state = {
            "Values": {"projections": [_projection(entity, measures[0], measure=True)]}
        }
    elif visual_type in {"pieChart", "donutChart"}:
        query_state = {
            "Category": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
            "Y": {
                "projections": [_projection(entity, measures[0], measure=True)] if measures else []
            },
        }
    elif visual_type == "hundredPercentStackedColumnChart":
        query_state = {
            "Category": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
            "Series": {
                "projections": [_projection(entity, dims[1], measure=False)] if len(dims) > 1 else []
            },
            "Y": {
                "projections": [_projection(entity, m, measure=True) for m in measures[:3]]
            },
        }
    elif visual_type == "lineClusteredColumnComboChart":
        query_state = {
            "Category": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
            "Y": {
                "projections": [_projection(entity, measures[0], measure=True)] if measures else []
            },
            "Y2": {
                "projections": [_projection(entity, measures[1], measure=True)]
                if len(measures) > 1
                else []
            },
        }
    else:
        # tableEx — Values holds columns + measures
        projs = [_projection(entity, d, measure=False) for d in dims[:6]]
        projs += [_projection(entity, m, measure=True) for m in measures[:6]]
        query_state = {"Values": {"projections": projs}}
        visual_type = "tableEx"

    return {
        "$schema": schema,
        "name": name,
        "position": {
            "x": x,
            "y": y,
            "z": z,
            "height": 360 if visual_type != "card" else 160,
            "width": 560 if visual_type != "card" else 280,
            "tabOrder": z,
        },
        "visual": {
            "visualType": visual_type,
            "drillFilterOtherVisuals": True,
            "query": {"queryState": query_state},
        },
    }


def _entity_for_worksheet(ws: WorksheetInfo, result: TranspileResult) -> str:
    ds_name = ws.datasource
    for ds in result.datasources:
        if ds.name == ds_name or ds.caption == ds_name:
            return (ds.caption or ds.name or "Table").strip()
    if result.datasources:
        ds = next((d for d in result.datasources if (d.name or "").lower() != "parameters"), None)
        if ds:
            return (ds.caption or ds.name or "Table").strip()
    return "Table"


def build_pbir_files(result: TranspileResult, report_folder: str) -> dict[str, str]:
    """Return zip paths → contents for PBIR definition/ tree."""

    files: dict[str, str] = {}
    base = f"{report_folder}/definition"
    files[f"{base}/version.json"] = render_json_template("version.json", {})
    files[f"{base}/report.json"] = render_json_template("report.definition.json", {})

    worksheets = list(getattr(result, "worksheets", None) or [])
    if not worksheets:
        # One placeholder page when no worksheet metadata
        page_name = "page1"
        files[f"{base}/pages/pages.json"] = render_template(
            "pages.json",
            {
                "PAGE_ORDER_JSON": json.dumps([page_name]),
                "ACTIVE_PAGE": page_name,
            },
        )
        # render_template leaves JSON; pretty-print
        files[f"{base}/pages/pages.json"] = (
            json.dumps(json.loads(files[f"{base}/pages/pages.json"]), indent=2) + "\n"
        )
        files[f"{base}/pages/{page_name}/page.json"] = render_json_template(
            "page.json",
            {
                "PAGE_NAME": page_name,
                "PAGE_DISPLAY": "Converted",
            },
        )
        entity = _entity_for_worksheet(
            WorksheetInfo(name="Converted"), result
        )
        measures = [
            f.caption
            for f in result.calculated_fields
            if f.dax_kind == "measure" and f.dax_status == ParseStatus.ok
        ][:2]
        visual = _build_visual_json(
            visual_type="card" if measures else "tableEx",
            entity=entity,
            dims=[],
            measures=measures or [],
            x=40,
            y=40,
            z=1000,
        )
        vid = visual["name"]
        files[f"{base}/pages/{page_name}/visuals/{vid}/visual.json"] = (
            json.dumps(visual, indent=2, ensure_ascii=False) + "\n"
        )
        return files

    page_ids: list[str] = []
    for idx, ws in enumerate(worksheets[:12]):
        pid = _page_id(ws.name)
        page_ids.append(pid)
        files[f"{base}/pages/{pid}/page.json"] = render_json_template(
            "page.json",
            {"PAGE_NAME": pid, "PAGE_DISPLAY": (ws.name or f"Sheet {idx + 1}")[:100]},
        )
        entity = _entity_for_worksheet(ws, result)
        dims: list[str] = []
        measures: list[str] = []
        for raw in list(ws.rows) + list(ws.cols) + list(ws.used_fields):
            cap, is_meas = _resolve_caption(raw, result.calculated_fields, entity)
            if is_meas:
                if cap not in measures:
                    measures.append(cap)
            else:
                if cap not in dims and cap not in measures:
                    dims.append(cap)
        # Prefer worksheet dimensions list
        for d in ws.dimensions:
            cap = _field_caption(d)
            if cap not in dims and cap not in measures:
                dims.insert(0, cap)
        vtype = _choose_visual_type(ws, len(dims), len(measures))
        visual = _build_visual_json(
            visual_type=vtype,
            entity=entity,
            dims=dims[:4],
            measures=measures[:4],
            x=40,
            y=40,
            z=1000,
        )
        vid = visual["name"]
        files[f"{base}/pages/{pid}/visuals/{vid}/visual.json"] = (
            json.dumps(visual, indent=2, ensure_ascii=False) + "\n"
        )
        # Caveat note file for mapping limits
        if vtype != "tableEx" and not (_VENDOR_VISUALS / f"{vtype}.visual.json").is_file():
            pass  # pattern synthesized; vendor file optional

    if not page_ids:
        page_ids = ["page1"]
        files[f"{base}/pages/page1/page.json"] = render_json_template(
            "page.json", {"PAGE_NAME": "page1", "PAGE_DISPLAY": "Converted"}
        )

    pages_body = {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/item/report/"
            "definition/pagesMetadata/1.0.0/schema.json"
        ),
        "pageOrder": page_ids,
        "activePageName": page_ids[0],
    }
    files[f"{base}/pages/pages.json"] = json.dumps(pages_body, indent=2) + "\n"
    return files


def visual_caveats() -> list[str]:
    return [
        "Stage 3 maps Tableau worksheets → PBIR visuals using Franssens pbi.kb patterns "
        "(card / pie / stacked column / combo / tableEx). Mark-class coverage is partial.",
        "Complex dual-axis, reference lines, and dashboard layout are approximated — "
        "one primary visual per worksheet page.",
        "pbir-utils was evaluated and not taken as a dependency; templates are vendored.",
    ]
