"""Stage 2: worksheet table-calc → DAX measure approximations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from engine.schema import ParseStatus
from engine.transpiler import dax_column_ref, dax_table_ident


@dataclass
class TableCalcDax:
    dax: str
    status: ParseStatus
    disposition: str  # converted | approximated | skipped
    reason: str


_ALONG_RE = re.compile(
    r"^(?P<label>.+?)\s+(?:(?P<agg>SUM|AVG|MIN|MAX|COUNT|COUNTD)\((?P<field>[^)]+)\))\s+along\s+(?P<addr>.+)$",
    re.IGNORECASE,
)
_ALONG_SIMPLE_RE = re.compile(
    r"^(?P<label>.+?)\s+(?P<field>.+?)\s+along\s+(?P<addr>.+)$",
    re.IGNORECASE,
)


def _measure_ref(field: str, agg: str | None, dax_table: str) -> str:
    """Build a DAX agg/measure ref — never ``SUM('Col')`` without a table."""

    caption = (field or "Value").strip().strip("[]") or "Value"
    if agg:
        fn = agg.upper()
        if fn == "COUNTD":
            return f"DISTINCTCOUNT({dax_column_ref(dax_table, caption)})"
        if fn == "AVG":
            return f"AVERAGE({dax_column_ref(dax_table, caption)})"
        return f"{fn}({dax_column_ref(dax_table, caption)})"
    return f"[{caption}]"


def transpile_table_calc_formula(formula: str, *, dax_table: str = "Orders") -> TableCalcDax:
    """Map Tableau worksheet table-calc description strings to DAX."""

    text = (formula or "").strip()
    match = _ALONG_RE.match(text) or _ALONG_SIMPLE_RE.match(text)
    if not match:
        return TableCalcDax(
            dax=f"// SKIPPED [table calc]: {text}",
            status=ParseStatus.skipped,
            disposition="skipped",
            reason="Unrecognized table calc formula shape",
        )
    label = match.group("label").strip().lower()
    agg = match.groupdict().get("agg")
    field = match.group("field").strip()
    addr = match.group("addr").strip()
    measure = _measure_ref(field, agg, dax_table)
    table = dax_table_ident(dax_table)

    if label.startswith("rank"):
        dax = f"RANKX(ALLSELECTED({table}), {measure}, , DESC, DENSE)"
        return TableCalcDax(
            dax=dax,
            status=ParseStatus.ok,
            disposition="approximated",
            reason=f"RANKX over ALLSELECTED; Tableau addressing '{addr}' is approximate",
        )

    if "percent of total" in label or label.startswith("% of total"):
        dax = f"DIVIDE({measure}, CALCULATE({measure}, ALLSELECTED()))"
        return TableCalcDax(
            dax=dax,
            status=ParseStatus.ok,
            disposition="approximated",
            reason=f"% of Total via DIVIDE + ALLSELECTED; addressing '{addr}' approximate",
        )

    if "running" in label or label.startswith("running sum"):
        dax = (
            f"CALCULATE({measure}, FILTER(ALLSELECTED({table}), "
            f"RANKX(ALLSELECTED({table}), {measure}, , ASC, DENSE) "
            f"<= RANKX(ALLSELECTED({table}), {measure}, , ASC, DENSE)))"
        )
        return TableCalcDax(
            dax=dax,
            status=ParseStatus.ok,
            disposition="approximated",
            reason=f"Running sum approximated with FILTER/RANKX; addressing '{addr}'",
        )

    if label in {"window", "lookup", "previous value"} or "window" in label:
        return TableCalcDax(
            dax=f"// SKIPPED [table calc window/offset]: {text}",
            status=ParseStatus.skipped,
            disposition="skipped",
            reason="WINDOW/OFFSET table calcs are not emitted in Stage 2",
        )

    if "index" in label:
        return TableCalcDax(
            dax=f"// SKIPPED [table calc index]: {text}",
            status=ParseStatus.skipped,
            disposition="skipped",
            reason="INDEX() table calcs are flagged, not translated",
        )

    return TableCalcDax(
        dax=f"// SKIPPED [table calc]: {text}",
        status=ParseStatus.skipped,
        disposition="skipped",
        reason=f"Unsupported table calc type '{label}'",
    )
