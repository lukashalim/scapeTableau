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
_INTERNAL_FIELD = re.compile(
    r"^(dataengine\.|[^:]*:Measure Names|:Measure Values|Measure Names|Measure Values|"
    r"Number of Records|__tableau)",
    re.IGNORECASE,
)


def _is_internal_field(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return True
    if _INTERNAL_FIELD.search(text):
        return True
    key = _field_caption(text)
    if key.lower() in {"measure names", "measure values", "number of records"}:
        return True
    if key.lower().startswith("dataengine."):
        return True
    return False


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


def _resolve_caption(
    raw: str,
    fields: list[CalculatedField],
    model_columns: set[str],
    model_measures: set[str],
) -> tuple[str, bool] | None:
    """Return (caption, is_measure) or None to skip (internal / unresolved)."""

    if _is_internal_field(raw):
        return None
    key = _field_caption(raw)
    if _is_internal_field(key):
        return None
    for f in fields:
        if f.caption == key or f.name.strip("[]") == key or key in {f.caption, f.name}:
            is_meas = (f.dax_kind == "measure") or (
                f.dax_status == ParseStatus.ok and f.caption in model_measures
            )
            # Prefer real model names
            if is_meas and f.caption in model_measures:
                return f.caption, True
            if f.caption in model_columns or f.caption in model_measures:
                return f.caption, bool(is_meas) or f.caption in model_measures
            return None
    # Physical column on the entity
    if key in model_columns:
        lowered = (raw or "").lower()
        is_meas = any(tok in lowered for tok in (":qk", "sum:", "avg:", "min:", "max:", "cnt:", "ctd:"))
        # Aggregated shelf on a column → bind as measure if a measure with that caption exists
        if is_meas and key in model_measures:
            return key, True
        return key, False
    if key in model_measures:
        return key, True
    return None


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


_MEASURES_ENTITY = "__Measures"


def _projection(entity: str, prop: str, *, measure: bool) -> dict:
    # Measures live on the dedicated __Measures table in our TMDL model
    home = _MEASURES_ENTITY if measure else entity
    kind = "Measure" if measure else "Column"
    return {
        "field": {
            kind: {
                "Expression": {"SourceRef": {"Entity": home}},
                "Property": prop,
            }
        },
        "queryRef": f"{home}.{prop}",
        "nativeQueryRef": prop,
        **({"active": True} if measure else {}),
    }


def visual_is_data_bound(visual: dict) -> bool:
    """True when visual.json has at least one Column/Measure projection (not a textbox)."""

    vbody = visual.get("visual") or {}
    vtype = (vbody.get("visualType") or "").lower()
    if vtype in {"textbox", "shape", "actionbutton", "image"}:
        return False
    qs = ((vbody.get("query") or {}).get("queryState")) or {}
    for role in qs.values():
        if isinstance(role, dict) and role.get("projections"):
            return True
    return False


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
    # Prefer chart types that keep real field wells; never emit textbox/title-only
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
        if not projs and measures:
            projs = [_projection(entity, measures[0], measure=True)]
        if not projs and dims:
            projs = [_projection(entity, dims[0], measure=False)]
        query_state = {"Values": {"projections": projs}}
        visual_type = "tableEx"

    # Guard: never write an empty queryState (Desktop shows blank / title-only)
    if not any(
        isinstance(role, dict) and role.get("projections")
        for role in query_state.values()
    ):
        if measures:
            query_state = {
                "Values": {"projections": [_projection(entity, measures[0], measure=True)]}
            }
            visual_type = "card"
        elif dims:
            query_state = {
                "Values": {"projections": [_projection(entity, dims[0], measure=False)]}
            }
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


def _entity_for_worksheet(
    ws: WorksheetInfo,
    result: TranspileResult,
    table_name_by_ds: dict[str, str] | None = None,
) -> str:
    ds_name = ws.datasource
    if table_name_by_ds and ds_name and ds_name in table_name_by_ds:
        return table_name_by_ds[ds_name]
    for ds in result.datasources:
        if ds.name == ds_name or ds.caption == ds_name:
            if table_name_by_ds and ds.name in table_name_by_ds:
                return table_name_by_ds[ds.name]
            caption = (ds.caption or "").strip()
            if caption and not caption.lower().startswith("dataengine."):
                return caption
            return (ds.caption or ds.name or "Table").strip()
    if result.datasources:
        ds = next((d for d in result.datasources if (d.name or "").lower() != "parameters"), None)
        if ds:
            if table_name_by_ds and ds.name in table_name_by_ds:
                return table_name_by_ds[ds.name]
            return (ds.caption or ds.name or "Table").strip()
    return "Table"


def _any_model_fields(result: TranspileResult) -> tuple[list[str], list[str]]:
    """Fallback dims/measures from the whole model when a page would otherwise be empty."""

    measures = sorted(
        {
            f.caption
            for f in result.calculated_fields
            if f.dax_kind == "measure" and f.dax_status == ParseStatus.ok and f.caption
        }
    )[:2]
    dims: list[str] = []
    for ds in result.datasources:
        if (ds.name or "").lower() == "parameters":
            continue
        for col in ds.fields:
            if col.calculated:
                continue
            cap = (col.caption or col.name or "").strip("[]")
            if cap and not _is_internal_field(cap):
                dims.append(cap)
            if len(dims) >= 3:
                break
        if dims:
            break
    if not dims and not measures:
        dims = ["Column1"]
    return dims, measures


def _model_fields(
    result: TranspileResult, entity: str, table_name_by_ds: dict[str, str] | None
) -> tuple[set[str], set[str]]:
    """Return (columns, measures) for a PBI table entity."""

    columns: set[str] = set()
    measures: set[str] = set()
    for f in result.calculated_fields:
        if f.dax_kind == "measure" and f.dax_status == ParseStatus.ok:
            measures.add(f.caption)
    for ds in result.datasources:
        tname = None
        if table_name_by_ds and ds.name in table_name_by_ds:
            tname = table_name_by_ds[ds.name]
        else:
            tname = (ds.caption or ds.name or "").strip()
        if tname != entity:
            continue
        for col in ds.fields:
            if col.calculated:
                continue
            cap = (col.caption or col.name or "").strip("[]")
            if cap and not _is_internal_field(cap):
                columns.add(cap)
        for f in result.calculated_fields:
            ds_keys = {ds.name, ds.caption}
            if f.datasource in ds_keys and f.dax_kind != "measure":
                if f.caption and f.dax_status == ParseStatus.ok:
                    columns.add(f.caption)
    return columns, measures


def build_pbir_files(
    result: TranspileResult,
    report_folder: str,
    *,
    table_name_by_ds: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return zip paths → contents for PBIR definition/ tree."""

    files: dict[str, str] = {}
    base = f"{report_folder}/definition"
    files[f"{base}/version.json"] = render_json_template("version.json", {})
    files[f"{base}/report.json"] = render_json_template("report.definition.json", {})

    worksheets = list(getattr(result, "worksheets", None) or [])
    if not worksheets:
        page_name = "page1"
        files[f"{base}/pages/pages.json"] = (
            json.dumps(
                {
                    "$schema": (
                        "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                        "definition/pagesMetadata/1.0.0/schema.json"
                    ),
                    "pageOrder": [page_name],
                    "activePageName": page_name,
                },
                indent=2,
            )
            + "\n"
        )
        files[f"{base}/pages/{page_name}/page.json"] = render_json_template(
            "page.json",
            {"PAGE_NAME": page_name, "PAGE_DISPLAY": "Converted"},
        )
        entity = _entity_for_worksheet(WorksheetInfo(name="Converted"), result, table_name_by_ds)
        model_columns, model_measures = _model_fields(result, entity, table_name_by_ds)
        measures = sorted(model_measures)[:2]
        dims = sorted(model_columns)[:3]
        if not dims and not measures:
            dims, measures = _any_model_fields(result)
        visual = _build_visual_json(
            visual_type="card" if measures and not dims else "tableEx",
            entity=entity,
            dims=dims,
            measures=measures,
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
        entity = _entity_for_worksheet(ws, result, table_name_by_ds)
        model_columns, model_measures = _model_fields(result, entity, table_name_by_ds)
        dims: list[str] = []
        measures: list[str] = []
        for raw in list(ws.rows) + list(ws.cols) + list(ws.used_fields) + list(ws.dimensions):
            resolved = _resolve_caption(
                raw, result.calculated_fields, model_columns, model_measures
            )
            if resolved is None:
                continue
            cap, is_meas = resolved
            if is_meas:
                if cap not in measures:
                    measures.append(cap)
            else:
                if cap not in dims and cap not in measures:
                    dims.append(cap)
        # If shelves empty after filtering, bind first real columns / measures
        if not dims and not measures:
            dims = sorted(model_columns)[:3]
            measures = sorted(model_measures)[:2]
        if not dims and not measures:
            dims, measures = _any_model_fields(result)
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
        if not visual_is_data_bound(visual):
            fb_dims, fb_meas = _any_model_fields(result)
            visual = _build_visual_json(
                visual_type="tableEx",
                entity=entity,
                dims=dims[:4] or fb_dims,
                measures=measures[:4] or fb_meas,
                x=40,
                y=40,
                z=1000,
            )
        vid = visual["name"]
        files[f"{base}/pages/{pid}/visuals/{vid}/visual.json"] = (
            json.dumps(visual, indent=2, ensure_ascii=False) + "\n"
        )

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
