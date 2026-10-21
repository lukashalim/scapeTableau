"""DAX post-polish from Franssens translation-guide gotchas.

Primary: never leave compact ``'Table'[Col] = [Measure]`` filters inside CALCULATE —
hoist the measure into a VAR (illegal PLACEHOLDER error at query time).
"""

from __future__ import annotations

import re

# CALCULATE(expr, 'T'[C] = [Measure Name], ...) with measure on RHS
_COMPACT_FILTER = re.compile(
    r"CALCULATE\s*\((.+),\s*"
    r"((?:'(?:[^']|'')+'|\w+)\[[^\]]+\]\s*=\s*\[[^\]]+\]"
    r"(?:\s*,\s*(?:'(?:[^']|'')+'|\w+)\[[^\]]+\]\s*=\s*\[[^\]]+\])*)"
    r"\s*\)",
    re.IGNORECASE | re.DOTALL,
)

_ONE_FILTER = re.compile(
    r"((?:'(?:[^']|'')+'|\w+)\[[^\]]+\])\s*=\s*(\[[^\]]+\])",
    re.IGNORECASE,
)


def hoist_compact_measure_filters(dax: str) -> str:
    """Rewrite illegal compact measure filters to VAR + scalar compare."""

    if not dax or "[" not in dax or "CALCULATE" not in dax.upper():
        return dax

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        filters = match.group(2)
        vars_: list[str] = []
        new_filters: list[str] = []
        for i, fm in enumerate(_ONE_FILTER.finditer(filters), start=1):
            col, meas = fm.group(1), fm.group(2)
            # Literal RHS already fine — only hoist [Measure] form (no table prefix)
            if re.fullmatch(r"\[[^\]]+\]", meas.strip()):
                vname = f"_f{i}"
                vars_.append(f"VAR {vname} = {meas.strip()}")
                new_filters.append(f"{col} = {vname}")
            else:
                new_filters.append(fm.group(0))
        if not vars_:
            return match.group(0)
        var_block = "\n".join(vars_)
        calc = f"CALCULATE({expr}, {', '.join(new_filters)})"
        return f"{var_block}\nRETURN {calc}"

    return _COMPACT_FILTER.sub(repl, dax)


def percent_format_string(caption: str, datatype: str | None, dax: str) -> str | None:
    """Suggest 0.0% format for ratio-like measures (guide + Franssens examples)."""

    name = (caption or "").lower()
    upper = (dax or "").upper()
    if any(tok in name for tok in ("ratio", "rate", "%", "pct", "percent", "margin")):
        return '0.0%'
    if "DIVIDE(" in upper and any(tok in name for tok in ("profit", "sales", "growth", "quota")):
        if "ratio" in name or "rate" in name or name.endswith("%"):
            return '0.0%'
    return None


def uniquify_measure_name(caption: str, used: set[str], table_hint: str | None = None) -> str:
    """Avoid model-wide measure name collisions (guide: Number of Records → table-prefixed)."""

    base = (caption or "Measure").strip() or "Measure"
    if base not in used:
        used.add(base)
        return base
    prefix = (table_hint or "Table").strip() or "Table"
    candidate = f"{prefix} {base}"
    n = 2
    while candidate in used:
        candidate = f"{prefix} {base} {n}"
        n += 1
    used.add(candidate)
    return candidate
