"""DAX post-polish from Franssens translation-guide gotchas.

Primary: never leave compact ``'Table'[Col] = [Measure]`` filters inside CALCULATE —
hoist the measure into a VAR (illegal PLACEHOLDER error at query time).

Also includes a small safe heal from cyphou/TableauToPowerBI (MIT): Tableau ``==`` → DAX ``=``.
See THIRD_PARTY_NOTICES.
"""

from __future__ import annotations

import re


def heal_double_equals(dax: str) -> str:
    """Convert Tableau ``==`` equality to DAX ``=`` outside strings / brackets / table quotes.

    Adapted from cyphou ``dax_healing.heal_double_equals`` (MIT) — semantics-preserving only.
    """

    if not dax or "==" not in dax:
        return dax
    out: list[str] = []
    i, n = 0, len(dax)
    while i < n:
        ch = dax[i]
        if ch == "[":
            start = i
            i += 1
            while i < n:
                if dax[i] == "]" and i + 1 < n and dax[i + 1] == "]":
                    i += 2
                    continue
                if dax[i] == "]":
                    i += 1
                    break
                i += 1
            out.append(dax[start:i])
            continue
        if ch in ('"', "'"):
            q = ch
            start = i
            i += 1
            while i < n:
                if dax[i] == q and i + 1 < n and dax[i + 1] == q:
                    i += 2
                    continue
                if dax[i] == q:
                    i += 1
                    break
                i += 1
            out.append(dax[start:i])
            continue
        if (
            ch == "="
            and i + 1 < n
            and dax[i + 1] == "="
            and (i == 0 or dax[i - 1] not in "<>!=")
        ):
            out.append("=")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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


def polish_dax(dax: str) -> str:
    """Apply safe Desktop-oriented DAX polishes in order."""

    text = heal_double_equals(dax or "")
    return hoist_compact_measure_filters(text)


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
