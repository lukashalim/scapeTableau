"""Stage 3: emit real PBIR definition/ pages + visuals from Tableau worksheets.

Visual JSON patterns follow vendored Franssens pbi.kb / tableEx templates under
``vendor/franssens-tableau-to-powerbi/templates/visuals/``. We do not depend on
pbir-utils — field binding is filled here.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from pathlib import Path

from engine.pbip.render import render_json_template, render_template
from engine.pbip.dashboard import build_dashboard_page
from engine.pbip.status import FULL, PARTIAL, DashboardOutcome, WorksheetOutcome
from engine.schema import CalculatedField, ParseStatus, TranspileResult, WorksheetInfo

_VENDOR_VISUALS = (
    Path(__file__).resolve().parents[2]
    / "vendor"
    / "franssens-tableau-to-powerbi"
    / "templates"
    / "visuals"
)

MAX_PAGES = 60  # one page per worksheet; anything beyond is listed in CONVERSION_STATUS.md

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


def _flat_name_map(result: TranspileResult) -> dict[str, str]:
    """Tableau internal column name -> caption, merged over every datasource."""

    flat: dict[str, str] = {}
    for per_ds in (getattr(result, "name_maps", None) or {}).values():
        for internal, caption in per_ds.items():
            flat.setdefault(internal.strip("[]"), caption)
    return flat


_CONSTANT_FORMULA = re.compile(r"^\s*(?:(?:MIN|MAX|SUM|AVG)\(\s*)?-?\d+(?:\.\d+)?\s*\)?\s*$", re.IGNORECASE)


def _constant_calc(raw: str, ws: WorksheetInfo, result: TranspileResult) -> bool:
    """True for a calculated field like ``MIN(1)`` that only exists to size marks."""

    key = _field_caption(raw)
    if key in ws.constant_fields:
        return True
    for f in result.calculated_fields:
        if key in {f.caption, (f.name or "").strip("[]")}:
            return bool(_CONSTANT_FORMULA.match(f.formula or ""))
    for ds in result.datasources:
        for col in ds.fields:
            if col.calculated and key in {col.caption, (col.name or "").strip("[]")}:
                return bool(_CONSTANT_FORMULA.match(col.formula or ""))
    return False


_LABEL_FORMULA = re.compile(r"""^\s*(?:"([^"]*)"|'([^']*)')\s*$""")


def _constant_label(raw: str, result: TranspileResult) -> tuple[str, str] | None:
    """``(caption, text)`` of a calculated field that is just a string literal.

    Tableau KPI sheets put such a calc (``"Total Hotels"``) on the Text shelf as the
    caption shown next to the number; it is a label, not a category.
    """

    key = _field_caption(raw)

    def _text(formula: str | None) -> str | None:
        m = _LABEL_FORMULA.match(formula or "")
        if not m:
            return None
        return m.group(1) if m.group(1) is not None else m.group(2)

    for f in result.calculated_fields:
        if key in {f.caption, (f.name or "").strip("[]")}:
            text = _text(f.formula)
            return (f.caption or key, text) if text is not None else None
    for ds in result.datasources:
        for col in ds.fields:
            if col.calculated and key in {col.caption, (col.name or "").strip("[]")}:
                text = _text(col.formula)
                return (col.caption or key, text) if text is not None else None
    return None


def _resolve_caption(
    raw: str,
    fields: list[CalculatedField],
    model_columns: set[str],
    model_measures: set[str],
    name_map: dict[str, str] | None = None,
) -> tuple[str, bool] | None:
    """Return (caption, is_measure) or None to skip (internal / unresolved)."""

    key = _field_caption(raw)
    in_model = key in model_columns or key in model_measures
    if _is_internal_field(raw) and not (in_model and key.lower() == "number of records"):
        return None
    if _is_internal_field(key) and not (in_model and key.lower() == "number of records"):
        return None
    if key not in model_columns and key not in model_measures:
        # The shelf carries Tableau's internal name ([country]); the model uses the caption.
        mapped = (name_map or {}).get(key)
        if mapped and (mapped in model_columns or mapped in model_measures):
            key = mapped
        else:
            lowered = key.lower()
            flat = re.sub(r"[^a-z0-9]", "", lowered)
            candidates = (*model_columns, *model_measures)
            hit = next((k for k in candidates if k.lower() == lowered), None)
            if hit is None and flat:
                # Doubled headers from the source CSV ('Profit Profit') still mean 'Profit'
                hit = next(
                    (k for k in candidates if re.sub(r"[^a-z0-9]", "", k.lower()) == flat * 2),
                    None,
                )
            if hit is not None:
                key = hit
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


# Tableau derivation -> Power BI aggregation function id
_AGG_FUNCTIONS = {"sum": 0, "avg": 1, "min": 2, "max": 3, "cnt": 4, "count": 4, "ctd": 5, "countd": 5}


def _agg_function(ws: WorksheetInfo, caption: str) -> int | None:
    """Power BI aggregation id for a plain aggregated shelf pill (``[sum:Sales:qk]``)."""

    return _AGG_FUNCTIONS.get(ws.aggregations.get(caption, ""))


def _measures_on_columns(ws: WorksheetInfo) -> bool:
    """True when the aggregated pill sits on Columns (bars run horizontally)."""

    return any(_agg_function(ws, c) is not None for c in ws.cols) and not any(
        _agg_function(ws, r) is not None for r in ws.rows
    )


_GEO_PRIORITY = ("state", "state/province", "province", "city", "postal code", "county", "country")
_FILLED_MARKS = {"multipolygon", "polygon"}


def _is_geo_map(ws: WorksheetInfo) -> bool:
    """Map sheet: filled/symbol map mark, or Tableau's generated Latitude/Longitude on shelves."""

    mark = (getattr(ws, "mark_class", None) or "").lower()
    if mark in _FILLED_MARKS or mark == "map":
        return True
    shelf = " ".join(list(ws.rows) + list(ws.cols)).lower()
    return "latitude (generated)" in shelf and "longitude (generated)" in shelf


def _map_location(
    ws: WorksheetInfo,
    dims: list[str],
    model_columns: set[str],
    result: TranspileResult | None = None,
) -> str | None:
    """Column that places marks on the map: Detail pill first, then a geographic name."""

    for raw in ws.detail_fields:
        key = _field_caption(raw)
        if key in model_columns:
            return key
        if result is not None:
            # The pill may carry Tableau's name ("country") while the model uses the
            # CSV header's case ("Country"); resolve like any other shelf field.
            resolved = _resolve_caption(raw, result.calculated_fields, model_columns, set(), _flat_name_map(result))
            if resolved and not resolved[1]:
                return resolved[0]
    by_lower = {d.lower(): d for d in dims}
    for name in _GEO_PRIORITY:
        if name in by_lower:
            return by_lower[name]
    return None


# Visuals whose builders can bind an aggregated physical column (SUM([Sales]) on a shelf)
_AGG_CAPABLE = {
    "clusteredBarChart",
    "clusteredColumnChart",
    "lineChart",
    "areaChart",
    "scatterChart",
    "card",
    "multiRowCard",
    "pieChart",
    "pivotTable",
    "filledMap",
    "map",
    "shapeMap",
}
# Visuals with a legend/Series well that a Tableau Color dimension can feed
_SERIES_VISUALS = {
    "clusteredBarChart",
    "clusteredColumnChart",
    "lineChart",
    "areaChart",
    "scatterChart",
}
_DATE_TYPES = {"date", "datetime"}


def _date_columns(
    result: TranspileResult, entity: str, table_name_by_ds: dict[str, str] | None
) -> set[str]:
    """Captions of date/datetime columns on the entity (a temporal axis means a line chart)."""

    out: set[str] = set()
    for ds in result.datasources:
        tname = (
            table_name_by_ds[ds.name]
            if table_name_by_ds and ds.name in table_name_by_ds
            else (ds.caption or ds.name or "").strip()
        )
        if tname != entity:
            continue
        for col in ds.fields:
            if (col.datatype or "").lower() in _DATE_TYPES:
                cap = (col.caption or col.name or "").strip("[]")
                if cap:
                    out.add(cap)
    return out


def _color_dimension(
    ws: WorksheetInfo,
    result: TranspileResult,
    model_columns: set[str],
    model_measures: set[str],
    *,
    exclude: list[str],
) -> str | None:
    """First dimension on Tableau's Color encoding -> the visual's legend (Series) well."""

    for raw in ws.encodings.get("color", []):
        resolved = _resolve_caption(raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result))
        if resolved and not resolved[1] and resolved[0] not in exclude:
            return resolved[0]
    return None


def _size_measure(
    ws: WorksheetInfo,
    result: TranspileResult,
    model_columns: set[str],
    model_measures: set[str],
    already_used: list[str],
) -> str | None:
    """Measure on Tableau's Size encoding -> scatter bubble size (when it is not an axis)."""

    for raw in ws.encodings.get("size", []):
        resolved = _resolve_caption(raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result))
        if resolved and resolved[1] and resolved[0] not in already_used[:2]:
            return resolved[0]
    return None


def _scatter_order(
    ws: WorksheetInfo,
    result: TranspileResult,
    measures: list[str],
    agg_columns: dict[str, int],
    model_columns: set[str],
    model_measures: set[str],
) -> tuple[list[str], dict[str, int]]:
    """Order values so Columns feed X and Rows feed Y (Tableau's scatter convention)."""

    rank: dict[str, int] = {}
    for position, raw in enumerate(list(ws.cols) + list(ws.rows)):
        resolved = _resolve_caption(raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result))
        if resolved:
            rank.setdefault(resolved[0], position)
    ordered = sorted(measures, key=lambda m: rank.get(m, 999))
    ordered_agg = dict(sorted(agg_columns.items(), key=lambda kv: rank.get(kv[0], 999)))
    return ordered, ordered_agg


def _is_state_location(name: str | None) -> bool:
    return (name or "").strip().lower() in {"state", "province", "state or province", "state/province"}


def _integer_label_format(ws: WorksheetInfo) -> bool:
    """True when Tableau asked for grouped digits and no decimal or thousands suffix."""

    for fmt in ws.number_formats.values():
        body = fmt.lstrip("*")
        if "#,##0" in body or body in {"#,0", "#,##0"}:
            return True
    return False


def _choose_visual_type(
    ws: WorksheetInfo, n_dims: int, n_meas: int, *, temporal: bool = False
) -> str:
    mark = (getattr(ws, "mark_class", None) or "").lower()
    if _is_geo_map(ws) and n_dims >= 1:
        # Automatic + a Geometry encoding is Tableau's filled (polygon) map.
        if mark in _FILLED_MARKS or getattr(ws, "has_geometry", False):
            return "filledMap"
        return "map"
    if mark in {"pie"}:
        return "pieChart"
    if mark in {"circle", "shape"} and n_meas >= 2:
        return "scatterChart"
    if mark in {"circle"} and n_meas <= 1:
        return "card"
    if mark == "area" and n_dims >= 1 and n_meas >= 1:
        return "areaChart"
    if mark == "line" and n_dims >= 1 and n_meas >= 1:
        return "lineChart"
    if mark == "automatic" and temporal and n_dims >= 1 and n_meas >= 1:
        return "lineChart"
    # Tableau's "Automatic" resolves to bars for categorical dimension + measure.
    if mark in {"bar", "ganttbar", "automatic"} and n_dims == 1 and n_meas >= 1:
        return "clusteredBarChart" if _measures_on_columns(ws) else "clusteredColumnChart"
    if mark in {"bar", "ganttbar"}:
        return "hundredPercentStackedColumnChart" if n_dims >= 2 else "tableEx"
    if mark in {"line", "area"}:
        return "lineClusteredColumnComboChart" if n_meas >= 2 else "stackedAreaChart"
    if mark in {"square"} and n_dims >= 1 and n_meas >= 1:
        return "pivotTable"
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
    }


def _aggregated_projection(entity: str, column: str, function: int) -> dict:
    """Explicit SUM/AVG/... over a physical column (what Desktop writes for chart values)."""

    return {
        "field": {
            "Aggregation": {
                "Expression": {
                    "Column": {
                        "Expression": {"SourceRef": {"Entity": entity}},
                        "Property": column,
                    }
                },
                "Function": function,
            }
        },
        "queryRef": f"{_AGG_NAMES[function]}({entity}.{column})",
        "nativeQueryRef": f"{_AGG_LABELS[function]} of {column}",
    }


_AGG_NAMES = {0: "Sum", 1: "Avg", 2: "Min", 3: "Max", 4: "Count", 5: "CountNonNull"}
_AGG_LABELS = {0: "Sum", 1: "Average", 2: "Min", 3: "Max", 4: "Count", 5: "Count (Distinct)"}


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
    agg_columns: dict[str, int] | None = None,
    filters: list[dict] | None = None,
    width: float | None = None,
    height: float | None = None,
    series: str | None = None,
    size_measure: str | None = None,
) -> dict:
    name = _visual_id()
    agg_columns = agg_columns or {}
    schema = (
        "https://developer.microsoft.com/json-schemas/fabric/item/report/"
        "definition/visualContainer/2.9.0/schema.json"
    )
    # Prefer chart types that keep real field wells; never emit textbox/title-only
    if visual_type == "card" and measures:
        query_state = {
            "Values": {"projections": [_projection(entity, measures[0], measure=True)]}
        }
    elif visual_type == "card" and agg_columns:
        col, fn = next(iter(agg_columns.items()))
        query_state = {"Values": {"projections": [_aggregated_projection(entity, col, fn)]}}
    elif visual_type in {"filledMap", "map", "shapeMap"}:
        # Location = Category. A filled map colours by Y. A shape map's colour
        # saturation well is Gradient; a measure left in Y is not part of the
        # visual's query, and a fill rule that references it fails to load.
        # A symbol map uses the measure as the bubble size.
        value_role = {"map": "Size", "shapeMap": "Gradient"}.get(visual_type, "Y")
        value_projs = [_projection(entity, m, measure=True) for m in measures[:1]]
        if not value_projs:
            value_projs = [
                _aggregated_projection(entity, col, fn) for col, fn in list(agg_columns.items())[:1]
            ]
        query_state = {
            "Category": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
        }
        if value_projs:
            query_state[value_role] = {"projections": value_projs}
    elif visual_type in {"pieChart", "donutChart"}:
        pie_vals = [_projection(entity, m, measure=True) for m in measures[:1]]
        if not pie_vals:
            pie_vals = [
                _aggregated_projection(entity, col, fn) for col, fn in list(agg_columns.items())[:1]
            ]
        query_state = {
            "Category": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
            "Y": {"projections": pie_vals},
        }
    elif visual_type == "multiRowCard":
        card_vals = [_projection(entity, m, measure=True) for m in measures[:6]]
        card_vals += [
            _aggregated_projection(entity, col, fn) for col, fn in list(agg_columns.items())[:6]
        ]
        query_state = {"Values": {"projections": card_vals}}
    elif visual_type == "pivotTable":
        matrix_vals = [_projection(entity, m, measure=True) for m in measures[:1]]
        if not matrix_vals:
            matrix_vals = [
                _aggregated_projection(entity, col, fn) for col, fn in list(agg_columns.items())[:1]
            ]
        query_state = {
            "Rows": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
            "Columns": {
                "projections": [_projection(entity, dims[1], measure=False)] if len(dims) > 1 else []
            },
            "Values": {"projections": matrix_vals},
        }
    elif visual_type in {"clusteredBarChart", "clusteredColumnChart", "lineChart", "areaChart"}:
        y_projs = [_projection(entity, m, measure=True) for m in measures[:3]]
        y_projs += [
            _aggregated_projection(entity, col, fn) for col, fn in list(agg_columns.items())[:3]
        ]
        series_name = series or (dims[1] if len(dims) > 1 else None)
        query_state = {
            "Category": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
            "Series": {
                "projections": [_projection(entity, series_name, measure=False)]
                if series_name
                else []
            },
            "Y": {"projections": y_projs},
        }
    elif visual_type == "scatterChart":
        # Columns feed X, Rows feed Y; a Detail dimension becomes the point Category
        # (the shape Franssens' Desktop-built superstore scatter uses).
        vals = [_projection(entity, m, measure=True) for m in measures]
        vals += [_aggregated_projection(entity, col, fn) for col, fn in (agg_columns or {}).items()]
        query_state = {
            "Category": {
                "projections": [_projection(entity, dims[0], measure=False)] if dims else []
            },
            "X": {"projections": vals[:1]},
            "Y": {"projections": vals[1:2]},
            "Size": {
                "projections": [_projection(entity, size_measure, measure=True)]
                if size_measure
                else []
            },
            "Series": {
                "projections": [_projection(entity, series, measure=False)] if series else []
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

    container: dict = {
        "$schema": schema,
        "name": name,
        "position": {
            "x": x,
            "y": y,
            "z": z,
            "height": height if height is not None else (360 if visual_type != "card" else 160),
            "width": width if width is not None else (560 if visual_type != "card" else 280),
            "tabOrder": z,
        },
        "visual": {
            "visualType": visual_type,
            "drillFilterOtherVisuals": True,
            "query": {"queryState": query_state},
        },
    }
    if filters:
        container["filterConfig"] = {"filters": filters}
    return container


def _categorical_filter(entity: str, column: str, members: list[str]) -> dict:
    """PBIR visual-level 'is one of' filter, mirroring a Tableau categorical member filter."""

    values = [
        [{"Literal": {"Value": "'" + member.replace("'", "''") + "'"}}] for member in members
    ]
    return {
        "name": _visual_id(),
        "field": {
            "Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": column}
        },
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "s", "Entity": entity, "Type": 0}],
            "Where": [
                {
                    "Condition": {
                        "In": {
                            "Expressions": [
                                {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Source": "s"}},
                                        "Property": column,
                                    }
                                }
                            ],
                            "Values": values,
                        }
                    }
                }
            ],
        },
    }


def _worksheet_filters(
    ws: WorksheetInfo,
    entity: str,
    result: TranspileResult,
    model_columns: set[str],
    model_measures: set[str],
) -> list[dict]:
    out: list[dict] = []
    for raw, members in ws.filters.items():
        resolved = _resolve_caption(raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result))
        if resolved is None or resolved[1]:
            continue  # unknown field, or a measure (needs a different filter shape)
        out.append(_categorical_filter(entity, resolved[0], members))
    return out


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


def _lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def _solid(hex_color: str) -> dict:
    return {"solid": {"color": _lit(f"'{hex_color}'")}}


_HEX6 = re.compile(r"^#[0-9a-fA-F]{6}$")
_ENCODING_SEED_ORDER = ("text", "wedge-size", "size", "color")


def _encoding_aggregation(ws: WorksheetInfo, raw: str) -> int | None:
    fn = ws.encoding_aggregations.get(raw) or ws.encoding_aggregations.get(_field_caption(raw))
    return _AGG_FUNCTIONS.get(fn or "")


def _seed_from_encodings(
    ws: WorksheetInfo,
    result: TranspileResult,
    model_columns: set[str],
    model_measures: set[str],
    dims: list[str],
    measures: list[str],
    agg_columns: dict[str, int],
    *,
    shelves_empty: bool,
) -> dict[str, tuple[str, str, str]]:
    """Bind Label / Wedge-size / Size / Color fields when the shelves leave the visual empty.

    Tableau KPI sheets and pies carry their only measure on a mark encoding, so without
    this the sheet has nothing to bind and falls back to a table of unrelated fields.
    Returns ``"<kind>:<caption>" -> (status, Power BI target, note)`` for what was bound.
    """

    applied: dict[str, tuple[str, str, str]] = {}
    is_pie = (ws.mark_class or "").lower() == "pie"
    need_value = not measures and not agg_columns
    if not need_value and not is_pie:
        return applied
    flat = _flat_name_map(result)
    for kind in _ENCODING_SEED_ORDER:
        for raw in ws.encodings.get(kind, []):
            if kind == "text":
                label = _constant_label(raw, result)
                if label is not None:
                    # A string literal on the Text shelf is the KPI's caption, not a category.
                    applied[f"text:{label[0]}"] = (
                        PARTIAL, "visual title", f'Label text "{label[1]}" shown as the visual title'
                    )
                    continue
            resolved = _resolve_caption(raw, result.calculated_fields, model_columns, model_measures, flat)
            if resolved is None:
                continue
            cap, is_meas = resolved
            key = f"{kind}:{cap}"
            target = {"text": "Values", "wedge-size": "Values", "size": "Values", "color": "Category"}[kind]
            if is_meas:
                if need_value and cap not in measures:
                    measures.append(cap)
                    applied[key] = (FULL, target, f"{kind} encoding supplies the visual's value")
                continue
            fn = _encoding_aggregation(ws, raw)
            if fn is not None and (need_value or is_pie) and not measures:
                if cap not in agg_columns:
                    agg_columns[cap] = fn
                    applied[key] = (FULL, f"{_AGG_LABELS[fn]} of [{cap}]", f"{kind} encoding supplies the value")
                continue
            wanted_dim = (kind == "text" and need_value) or (
                kind == "color" and (shelves_empty or is_pie)
            )
            if wanted_dim and cap not in dims:
                if is_pie and kind == "color":
                    dims.insert(0, cap)  # the slice dimension
                else:
                    dims.append(cap)
                applied[key] = (FULL, f"[{cap}]", f"{kind} encoding supplies the visual's category")
    return applied


def _find_field(visual: dict, caption: str) -> dict | None:
    """The ``field`` of the first projection whose property is ``caption``."""

    qs = (visual.get("visual", {}).get("query") or {}).get("queryState") or {}
    for role in qs.values():
        for proj in role.get("projections", []):
            field = proj.get("field", {})
            for kind in ("Column", "Measure"):
                body = field.get(kind)
                if body and body.get("Property") == caption:
                    return field
            agg = field.get("Aggregation")
            if agg and agg["Expression"]["Column"].get("Property") == caption:
                return field
    return None


def _apply_sort(
    visual: dict,
    ws: WorksheetInfo,
    result: TranspileResult,
    model_columns: set[str],
    model_measures: set[str],
) -> dict[str, str]:
    """Write Tableau's first resolvable sort into the visual's ``sortDefinition``."""

    flat = _flat_name_map(result)
    for spec in ws.sort_specs:
        candidates = [spec.get("using") or "", spec.get("field") or ""]
        for raw in candidates:
            if not raw:
                continue
            resolved = _resolve_caption(raw, result.calculated_fields, model_columns, model_measures, flat)
            if resolved is None:
                continue
            field = _find_field(visual, resolved[0])
            if field is None:
                continue
            descending = (spec.get("direction") or "ASC").upper().startswith("DESC")
            visual["visual"]["query"]["sortDefinition"] = {
                "sort": [{"field": field, "direction": "Descending" if descending else "Ascending"}],
                "isDefaultSort": True,
            }
            label = resolved[0]
            note = f"{'Descending' if descending else 'Ascending'} by [{label}]"
            if not spec.get("using"):
                note += " (manual member order approximated)"
            return {_field_caption(spec.get("field") or raw): note}
    return {}


def _luminance(color: str) -> float:
    """Perceived brightness of ``#RRGGBB`` from 0 (black) to 1 (white)."""

    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b


def _apply_map_gradient(objects: dict, body: dict) -> None:
    """Tableau's map blue-green: pale yellow for low counts, teal for high.

    Chart fill rules take a data-point wildcard and no metadata. A metadata
    selector is for tables; on a shape map it matches nothing, so every state
    stays the default blue.
    """

    projections = (
        ((body.get("query") or {}).get("queryState") or {}).get("Gradient", {}).get("projections") or []
    )
    if not projections or not projections[0].get("field"):
        return
    target = projections[0]
    objects["dataPoint"] = [
        {
            "properties": {
                "fill": {
                    "solid": {"color": {"expr": _gradient(target["field"], "#2E9B96", low="#F4F6C8")}}
                }
            },
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 0}}]},
        }
    ]


def _map_center_latitude(ws: WorksheetInfo) -> float:
    """Latitude at the middle of the viewport in Web Mercator, not the arithmetic mean."""

    def y_rad(lat: float) -> float:
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    mid = (y_rad(ws.map_south or 0) + y_rad(ws.map_north or 0)) / 2
    return math.degrees(2 * math.atan(math.exp(mid)) - math.pi / 2)


def _map_zoom(ws: WorksheetInfo, *, width: float, height: float) -> float:
    """Bing zoom that fits the Tableau viewport in this visual.

    Each zoom step halves the ground distance. The tile size is 256px.
    """

    def y_rad(lat: float) -> float:
        return math.log(math.tan(math.pi / 4 + math.radians(max(min(lat, 85.0), -85.0)) / 2))

    lng_frac = abs((ws.map_east or 0) - (ws.map_west or 0)) / 360 or 1
    lat_frac = abs(y_rad(ws.map_north or 0) - y_rad(ws.map_south or 0)) / (2 * math.pi) or 1
    # The layout size is in the 1920x1080 page. A zero size still needs a US-scale zoom.
    px_w = width if width and width > 1 else 900
    px_h = height if height and height > 1 else 500

    def level(px: float, frac: float) -> float:
        return math.log2(px / (256 * frac))

    return max(1.0, min(level(px_w, lng_frac), level(px_h, lat_frac), 16.0))


def _apply_formatting(
    visual: dict,
    ws: WorksheetInfo,
    *,
    series: str | None,
    width: float | None = 0,
    height: float | None = 0,
) -> list[str]:
    """Title, background, mark colour and data labels carried over from the worksheet."""

    applied: list[str] = []
    body = visual["visual"]
    vtype = body["visualType"]
    containers: dict = body.setdefault("visualContainerObjects", {})
    objects: dict = body.setdefault("objects", {})
    # Dark Tableau themes are not carried over: Power BI keeps its default (dark) text on the
    # visual, so a dark background would make values and axes unreadable. A light title
    # colour only makes sense on that dark background, so it is dropped with it.
    dark_theme = bool(
        ws.background_color and _HEX6.match(ws.background_color) and _luminance(ws.background_color) < 0.5
    )
    if ws.title:
        props: dict = {"show": _lit("true"), "text": _lit("'" + ws.title.replace("'", "''") + "'")}
        if ws.title_color and _HEX6.match(ws.title_color) and (
            not dark_theme and _luminance(ws.title_color) < 0.8
        ):
            props["fontColor"] = _solid(ws.title_color)
        containers["title"] = [{"properties": props}]
        applied.append("title")
    if ws.background_color and _HEX6.match(ws.background_color) and not dark_theme:
        containers["background"] = [
            {"properties": {"show": _lit("true"), "color": _solid(ws.background_color), "transparency": _lit("0D")}}
        ]
        applied.append("background")
    chart_types = {
        "clusteredBarChart", "clusteredColumnChart", "lineChart", "areaChart", "pieChart",
        "donutChart", "scatterChart", "hundredPercentStackedColumnChart",
        "lineClusteredColumnComboChart", "stackedAreaChart", "filledMap", "map", "shapeMap",
    }
    if (
        ws.mark_color
        and _HEX6.match(ws.mark_color)
        and _luminance(ws.mark_color) < 0.9  # near-white marks vanish on the white page
        and vtype in chart_types
        and not series
        and vtype != "pieChart"
    ):
        objects["dataPoint"] = [{"properties": {"fill": _solid(ws.mark_color)}}]
        applied.append("mark colour")
    if vtype == "shapeMap":
        objects["shape"] = [{"properties": {"map": _lit("'USA'")}}]
        applied.append("USA shape map")
        if (ws.color_palette or "") == "tableau-map-blue-green":
            _apply_map_gradient(objects, body)
            applied.append("blue-green palette")
    if vtype == "filledMap" and None not in (ws.map_west, ws.map_south, ws.map_east, ws.map_north):
        objects["mapControls"] = [
            {
                "properties": {
                    "autoZoom": _lit("false"),
                    "centerLatitude": _lit(f"{_map_center_latitude(ws):.4f}D"),
                    "centerLongitude": _lit(f"{((ws.map_west or 0) + (ws.map_east or 0)) / 2:.4f}D"),
                    "zoomLevel": _lit(f"{_map_zoom(ws, width=width, height=height):.2f}D"),
                }
            }
        ]
        applied.append("map extent")
    if vtype == "filledMap" and not ws.map_place_labels:
        objects["mapStyles"] = [{"properties": {"showLabels": _lit("false")}}]
        applied.append("map place labels off")
    wants_labels = ws.show_labels or bool(ws.encodings.get("text"))
    if wants_labels and vtype in chart_types:
        label_props: dict = {"show": _lit("true")}
        if _integer_label_format(ws):
            # 1 = None. Otherwise Power BI shortens 22745 to 22.7K.
            label_props["labelDisplayUnits"] = _lit("1D")
        objects["labels"] = [{"properties": label_props}]
        applied.append("data labels")
    if vtype in {"clusteredBarChart", "clusteredColumnChart"}:
        if not ws.show_field_labels:
            objects["categoryAxis"] = [{"properties": {"showAxisTitle": _lit("false")}}]
            applied.append("field labels off")
        if ws.hide_value_axis:
            objects["valueAxis"] = [
                {"properties": {"show": _lit("false"), "showAxisTitle": _lit("false")}}
            ]
            applied.append("value axis off")
        if not ws.mark_color:
            objects.setdefault("dataPoint", [{"properties": {"fill": _solid("#4E79A7")}}])
            applied.append("Tableau blue")
    if not objects:
        body.pop("objects", None)
    if not containers:
        body.pop("visualContainerObjects", None)
    return applied


def _gradient(field: dict, high: str, low: str = "#FFFFFF") -> dict:
    return {
        "FillRule": {
            "Input": field,
            "FillRule": {
                "linearGradient2": {
                    "min": {"color": {"Literal": {"Value": f"'{low}'"}}},
                    "max": {"color": {"Literal": {"Value": f"'{high}'"}}},
                    "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}},
                }
            },
        }
    }


_COLOR_BY_CATEGORY = {"clusteredBarChart", "clusteredColumnChart"}


def _apply_color_encoding(
    visual: dict,
    ws: WorksheetInfo,
    result: TranspileResult,
    model_columns: set[str],
    model_measures: set[str],
    dims: list[str],
    measures: list[str],
    series: str | None,
) -> dict[str, tuple[str, str, str]]:
    """Colour encodings that are not a Legend/Series: per-category colours or a colour scale."""

    applied: dict[str, tuple[str, str, str]] = {}
    vtype = visual["visual"]["visualType"]
    flat = _flat_name_map(result)
    for raw in ws.encodings.get("color", []):
        resolved = _resolve_caption(raw, result.calculated_fields, model_columns, model_measures, flat)
        if resolved is None:
            continue
        cap, is_meas = resolved
        key = f"color:{cap}"
        objects = visual["visual"].setdefault("objects", {})
        if vtype in _COLOR_BY_CATEGORY and not is_meas and dims and cap == dims[0] and cap != series:
            objects.setdefault("dataPoint", [{"properties": {}}])[0]["properties"][
                "showAllDataPoints"
            ] = _lit("true")
            applied[key] = (PARTIAL, "Vary colours by category", "Each category coloured; Tableau palette not carried over")
        elif vtype in _COLOR_BY_CATEGORY and is_meas and cap in measures:
            field = _find_field(visual, cap)
            if field is not None:
                high = ws.mark_color if ws.mark_color and _HEX6.match(ws.mark_color) else "#2E75B6"
                objects["dataPoint"] = [
                    {
                        "properties": {"fill": {"solid": {"color": {"expr": _gradient(field, high)}}}},
                        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]},
                    }
                ]
                applied[key] = (PARTIAL, f"Colour scale on [{cap}]", "Gradient applied; Tableau palette not carried over")
    return applied


def _apply_matrix_heat(visual: dict, ws: WorksheetInfo) -> bool:
    """Background-colour scale on the matrix's value field (Tableau highlight table)."""

    qs = visual["visual"]["query"]["queryState"]
    projections = (qs.get("Values") or {}).get("projections") or []
    if not projections:
        return False
    target = projections[0]
    high = ws.mark_color if ws.mark_color and _HEX6.match(ws.mark_color) else "#2E75B6"
    rule = {
        "Input": target["field"],
        "FillRule": {
            "linearGradient2": {
                "min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                "max": {"color": {"Literal": {"Value": f"'{high}'"}}},
                "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}},
            }
        },
    }
    visual["visual"].setdefault("objects", {})["values"] = [
        {
            "properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": rule}}}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": target["queryRef"]},
        }
    ]
    return True


def slicer_json(
    entity: str,
    column: str,
    *,
    x: float,
    y: float,
    z: int,
    width: float = 240,
    height: float = 80,
) -> dict:
    """A dropdown slicer on a physical column (Tableau quick filter / parameter control)."""

    return {
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/item/report/"
            "definition/visualContainer/2.9.0/schema.json"
        ),
        "name": _visual_id(),
        "position": {"x": x, "y": y, "z": z, "height": height, "width": width, "tabOrder": z},
        "visual": {
            "visualType": "slicer",
            "drillFilterOtherVisuals": True,
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {**_projection(entity, column, measure=False), "active": True}
                        ]
                    }
                }
            },
            "objects": {"data": [{"properties": {"mode": _lit("'Dropdown'")}}]},
        },
    }


def sheet_slicers(
    ws: WorksheetInfo,
    result: TranspileResult,
    table_name_by_ds: dict[str, str] | None,
    *,
    x: float,
    y: float,
    z: int,
) -> list[tuple[dict, str]]:
    """Slicers for the sheet's categorical filters that have no fixed member list."""

    entity = _entity_for_worksheet(ws, result, table_name_by_ds)
    model_columns, model_measures = _model_fields(result, entity, table_name_by_ds)
    out: list[tuple[dict, str]] = []
    for raw in ws.slicer_filters:
        resolved = _resolve_caption(
            raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result)
        )
        if resolved is None or resolved[1] or any(c == resolved[0] for _, c in out):
            continue
        out.append((slicer_json(entity, resolved[0], x=x, y=y + 90 * len(out), z=z + len(out)), resolved[0]))
        if len(out) >= 4:
            break
    return out



def _worksheet_visual(
    ws: WorksheetInfo,
    result: TranspileResult,
    table_name_by_ds: dict[str, str] | None,
    *,
    page_name: str,
    x: float = 40,
    y: float = 40,
    z: int = 1000,
    width: float | None = None,
    height: float | None = None,
) -> tuple[dict, WorksheetOutcome]:
    """Build the visual (and a record of what happened) for one Tableau worksheet."""

    entity = _entity_for_worksheet(ws, result, table_name_by_ds)
    model_columns, model_measures = _model_fields(result, entity, table_name_by_ds)
    dims: list[str] = []
    measures: list[str] = []
    agg_columns: dict[str, int] = {}
    dropped: list[tuple[str, str]] = []
    added_fields: list[str] = []
    for raw in list(ws.rows) + list(ws.cols) + list(ws.used_fields) + list(ws.dimensions):
        resolved = _resolve_caption(
            raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result)
        )
        if resolved is None:
            if not any(raw == d[0] for d in dropped):
                if _constant_calc(raw, ws, result):
                    reason = "Constant helper calculation (always 1); marks are counted by row instead"
                elif _is_internal_field(raw) or "(generated)" in raw:
                    reason = "Internal / generated field with no Power BI equivalent"
                else:
                    reason = "Not a column or measure in the Power BI model"
                dropped.append((raw, reason))
            continue
        cap, is_meas = resolved
        agg_fn = None
        if not is_meas:
            agg_fn = _agg_function(ws, cap)
            if agg_fn is None:
                agg_fn = _agg_function(ws, _field_caption(raw))
        if is_meas:
            if cap not in measures:
                measures.append(cap)
        elif agg_fn is not None:
            # SUM([Sales]) on a shelf: a physical column used as an aggregated value
            agg_columns.setdefault(cap, agg_fn)
        else:
            if cap not in dims and cap not in measures:
                dims.append(cap)
    shelves_empty = not dims and not measures and not agg_columns
    applied_encodings = _seed_from_encodings(
        ws, result, model_columns, model_measures, dims, measures, agg_columns,
        shelves_empty=shelves_empty,
    )
    if applied_encodings:
        shelves_empty = False
    # If shelves empty after filtering, bind first real columns / measures
    if not dims and not measures and not agg_columns:
        dims = sorted(model_columns)[:3]
        measures = sorted(model_measures)[:2]
    if not dims and not measures and not agg_columns:
        dims, measures = _any_model_fields(result)
    location: str | None = None
    if _is_geo_map(ws):
        location = _map_location(ws, dims, model_columns, result)
        if location:
            # The filter column (e.g. Region) must not displace the Location well
            dims = [location]
    date_columns = _date_columns(result, entity, table_name_by_ds)
    temporal = any(d in date_columns for d in dims)
    vtype = _choose_visual_type(
        ws, len(dims), len(measures) + len(agg_columns), temporal=temporal
    )
    # Bing's filled map paints every matched state one colour and leaves holes
    # where a name does not geocode. A state polygon sheet is the USA shape map:
    # each state is a real shape, coloured by the measure.
    if vtype == "filledMap" and _is_state_location(location or (dims[0] if dims else None)):
        vtype = "shapeMap"
    if agg_columns and vtype not in _AGG_CAPABLE:
        vtype = "tableEx"  # only these builders bind aggregated columns
    series: str | None = None
    size_measure: str | None = None
    if vtype == "pieChart":
        pie_color = _color_dimension(ws, result, model_columns, model_measures, exclude=[])
        if pie_color:
            if pie_color in dims:
                dims.remove(pie_color)
            dims.insert(0, pie_color)
            applied_encodings.setdefault(
                f"color:{pie_color}", (FULL, f"Legend / slice [{pie_color}]", "Color dimension defines the slices")
            )
    if vtype in _SERIES_VISUALS:
        series = _color_dimension(
            ws, result, model_columns, model_measures, exclude=dims[:1]
        )
    if vtype == "scatterChart":
        # Detail pills (one mark per member) become the scatter's Category
        detail: list[str] = []
        for raw in ws.detail_fields:
            resolved = _resolve_caption(
                raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result)
            )
            if resolved and not resolved[1] and resolved[0] not in detail:
                detail.append(resolved[0])
        dims = detail + [d for d in dims if d not in detail]
        measures, agg_columns = _scatter_order(
            ws, result, measures, agg_columns, model_columns, model_measures
        )
        size_measure = _size_measure(ws, result, model_columns, model_measures, measures)
    if vtype == "tableEx":
        # Aggregated physical columns have no chart well here; show them as columns.
        for col in agg_columns:
            if col not in dims:
                dims.append(col)
        # Row-level numbers the extract actually has. A table of only % of Total
        # measures shows headers and blank cells when those columns are empty.
        # Skipped when the sheet already names its own numeric column.
        if not agg_columns:
            for extra in ("Sales", "Profit"):
                if extra in model_columns and extra not in dims and extra not in measures:
                    dims.append(extra)
                    added_fields.append(extra)
    ws_filters = _worksheet_filters(ws, entity, result, model_columns, model_measures)
    visual = _build_visual_json(
        visual_type=vtype,
        entity=entity,
        dims=dims[:4],
        measures=measures[:4],
        x=x,
        y=y,
        z=z,
        width=width,
        height=height,
        agg_columns=agg_columns if vtype != "tableEx" else None,
        filters=ws_filters,
        series=series,
        size_measure=size_measure,
    )
    visual_fallback = False
    if not visual_is_data_bound(visual):
        visual_fallback = True
        ws_filters = []
        series = None
        fb_dims, fb_meas = _any_model_fields(result)
        visual = _build_visual_json(
            visual_type="tableEx",
            entity=entity,
            dims=dims[:4] or fb_dims,
            measures=measures[:4] or fb_meas,
            x=x,
            y=y,
            z=z,
            width=width,
            height=height,
        )
    final_type = visual["visual"]["visualType"]
    applied_sorts: dict[str, str] = {}
    formatting: list[str] = []
    if not visual_fallback:
        applied_sorts = _apply_sort(visual, ws, result, model_columns, model_measures)
        if not ws.title:
            # A string-literal calc on the Text shelf names the KPI; use it as the title.
            for raw in ws.encodings.get("text", []):
                label = _constant_label(raw, result)
                if label and label[1]:
                    ws = ws.model_copy(update={"title": label[1]})
                    break
        formatting = _apply_formatting(visual, ws, series=series, width=width, height=height)
        for k, v in _apply_color_encoding(
            visual, ws, result, model_columns, model_measures, dims, measures, series
        ).items():
            applied_encodings.setdefault(k, v)
        if final_type == "pieChart":
            flat = _flat_name_map(result)
            for kind in ("wedge-size", "size", "text", "color"):
                for raw in ws.encodings.get(kind, []):
                    resolved = _resolve_caption(
                        raw, result.calculated_fields, model_columns, model_measures, flat
                    )
                    if resolved is None:
                        continue
                    cap = resolved[0]
                    if kind == "color" and dims and cap == dims[0]:
                        applied_encodings.setdefault(f"color:{cap}", (FULL, f"Legend / slice [{cap}]", "Colour defines the slices"))
                    elif kind in {"wedge-size", "size"} and (
                        (measures and cap == measures[0]) or cap in agg_columns
                    ):
                        applied_encodings.setdefault(f"{kind}:{cap}", (FULL, f"Values [{cap}]", "Sets the slice size"))
        if final_type == "pivotTable" and _apply_matrix_heat(visual, ws):
            formatting.append("heat-map colour scale")
        if size_measure and final_type == "scatterChart":
            applied_encodings[f"size:{size_measure}"] = (FULL, f"Size [{size_measure}]", "")
        if "data labels" in formatting:
            for raw in ws.encodings.get("text", []):
                resolved = _resolve_caption(
                    raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result)
                )
                if resolved:
                    applied_encodings.setdefault(
                        f"text:{resolved[0]}", (FULL, "Data labels", "Data labels switched on")
                    )
        for raw in ws.encodings.get("text", []):
            resolved = _resolve_caption(
                raw, result.calculated_fields, model_columns, model_measures, _flat_name_map(result)
            )
            if resolved and resolved[0] in (dims + measures + list(agg_columns)) and final_type in {
                "card", "multiRowCard", "tableEx", "pivotTable"
            }:
                applied_encodings.setdefault(
                    f"text:{resolved[0]}", (FULL, f"[{resolved[0]}] in {final_type}", "Label shown as the visual's value")
                )
    outcome = WorksheetOutcome(
        name=ws.name,
        page_name=page_name,
        mark=ws.mark_class or "automatic",
        visual_type=final_type,
        entity=entity,
        dims=list(dims[:4]),
        measures=list(measures[:4]),
        agg_columns=dict(agg_columns) if final_type != "tableEx" else {},
        location=location if final_type in {"filledMap", "map", "shapeMap"} else None,
        applied_filters=[f["field"]["Column"]["Property"] for f in ws_filters],
        dropped=dropped,
        added_fields=added_fields,
        shelves_empty=shelves_empty,
        visual_fallback=visual_fallback,
        series=series if final_type in _SERIES_VISUALS else None,
        size_measure=size_measure if final_type == "scatterChart" else None,
        applied_encodings=applied_encodings,
        applied_sorts=applied_sorts,
        formatting_applied=formatting,
    )
    return visual, outcome


def build_pbir_files(
    result: TranspileResult,
    report_folder: str,
    *,
    table_name_by_ds: dict[str, str] | None = None,
    outcomes: list[WorksheetOutcome] | None = None,
    dashboard_outcomes: list[DashboardOutcome] | None = None,
) -> dict[str, str]:
    """Return zip paths → contents for PBIR definition/ tree.

    When ``outcomes`` is given, one :class:`WorksheetOutcome` per emitted page is
    appended so callers can report exactly what each worksheet became.
    """

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
    placed: set[str] = set()
    ws_by_name = {w.name: w for w in worksheets}

    # Dashboards first: one Power BI page per Tableau dashboard, sheets at their zone positions.
    for dash in getattr(result, "dashboards", None) or []:
        if len(page_ids) >= MAX_PAGES:
            break
        page = build_dashboard_page(
            dash,
            ws_by_name,
            result,
            table_name_by_ds,
            base=base,
            outcomes=outcomes,
            dashboard_outcomes=dashboard_outcomes,
        )
        if page is None:
            continue
        page_ids.append(page.page_id)
        files.update(page.files)
        placed.update(page.worksheets)

    # Worksheets not placed on any dashboard keep a page of their own.
    for idx, ws in enumerate(worksheets):
        if ws.name in placed or len(page_ids) >= MAX_PAGES:
            continue
        pid = _page_id(ws.name)
        page_ids.append(pid)
        files[f"{base}/pages/{pid}/page.json"] = render_json_template(
            "page.json",
            {"PAGE_NAME": pid, "PAGE_DISPLAY": (ws.name or f"Sheet {idx + 1}")[:100]},
        )
        visual, outcome = _worksheet_visual(ws, result, table_name_by_ds, page_name=pid)
        if outcomes is not None:
            outcomes.append(outcome)
        vid = visual["name"]
        files[f"{base}/pages/{pid}/visuals/{vid}/visual.json"] = (
            json.dumps(visual, indent=2, ensure_ascii=False) + "\n"
        )
        if not outcome.visual_fallback:
            for slicer, caption in sheet_slicers(ws, result, table_name_by_ds, x=640, y=40, z=2000):
                files[f"{base}/pages/{pid}/visuals/{slicer['name']}/visual.json"] = (
                    json.dumps(slicer, indent=2, ensure_ascii=False) + "\n"
                )
                outcome.slicers.append(caption)

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
        "Tableau worksheets map to PBIR visuals using Franssens pbi.kb patterns "
        "(bar / column / line / area / scatter / pie / map / card / tableEx). "
        "Mark-class coverage is partial; see CONVERSION_STATUS.md for each sheet.",
        "Each Tableau dashboard becomes one 1920x1080 page with sheets at their zone "
        "positions, text/background zones as textbox/shape, and button sheets as a "
        "page navigator. Parameter controls, images and quick filters are not placed.",
        "Dual-axis, bullet, radar and box-plot charts have no native equivalent and are "
        "approximated.",
        "pbir-utils was evaluated and not taken as a dependency; templates are vendored.",
    ]
