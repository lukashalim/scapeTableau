"""Post-build PBIP validator — schema, missing files, DAX refs vs model.

Fails the build (raises ``PbipValidationError``) when errors are found.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_SCHEMA_ROOT = (
    Path(__file__).resolve().parents[2] / "vendor" / "microsoft-json-schemas"
)

# 'Table'[Column] or Table[Column]
_DAX_COL_REF = re.compile(
    r"(?:'(?P<qtable>(?:[^']|'')+)'|(?P<table>[A-Za-z_][A-Za-z0-9_ ]*))\[(?P<col>[^\]]+)\]"
)
# Bare SUM('Sales') / AVERAGE('X') without table — illegal
_BARE_AGG = re.compile(
    r"\b(?P<fn>SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\(\s*'(?P<col>[^']+)'\s*\)",
    re.IGNORECASE,
)


class PbipValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("PBIP validation failed:\n" + "\n".join(f"- {e}" for e in errors))


@dataclass
class ModelCatalog:
    tables: set[str] = field(default_factory=set)
    columns: dict[str, set[str]] = field(default_factory=dict)  # table → columns
    measures: set[str] = field(default_factory=set)


def _load_schema(rel: str) -> dict | None:
    path = _SCHEMA_ROOT / rel
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_required(instance: dict, schema: dict, path: str, errors: list[str]) -> None:
    for key in schema.get("required") or []:
        if key not in instance:
            errors.append(f"{path}: missing required key '{key}'")


def _unquote_tmdl_ident(raw: str) -> str:
    """Decode a TMDL quoted identifier (``''`` → ``'``)."""

    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text.replace("''", "'")


def _parse_tmdl_model(files: dict[str, str]) -> ModelCatalog:
    cat = ModelCatalog()
    # Quoted idents may contain doubled apostrophes: 'Coût d''Achat'
    table_re = re.compile(r"^table\s+'((?:[^']|'')+)'|^table\s+(\S+)", re.M)
    col_re = re.compile(r"^\tcolumn\s+'((?:[^']|'')+)'|^\tcolumn\s+(\S+)", re.M)
    meas_re = re.compile(r"^\tmeasure\s+'((?:[^']|'')+)'|^\tmeasure\s+(\S+)", re.M)
    current: str | None = None
    for path, text in files.items():
        if not path.endswith(".tmdl"):
            continue
        for line in text.splitlines():
            tm = table_re.match(line)
            if tm:
                current = _unquote_tmdl_ident(tm.group(1) or tm.group(2) or "")
                if current:
                    cat.tables.add(current)
                    cat.columns.setdefault(current, set())
                continue
            if current is None:
                continue
            cm = col_re.match(line)
            if cm:
                col = cm.group(1) or cm.group(2)
                if col:
                    # strip trailing ` = expr` for calculated columns
                    if cm.group(1) is None and "=" in col:
                        col = col.split("=", 1)[0].strip()
                    col = _unquote_tmdl_ident(col)
                    cat.columns[current].add(col)
                continue
            mm = meas_re.match(line)
            if mm:
                m = mm.group(1) or mm.group(2)
                if m:
                    if mm.group(1) is None and "=" in m:
                        m = m.split("=", 1)[0].strip()
                    m = _unquote_tmdl_ident(m)
                    cat.measures.add(m)
    return cat


def _referenced_static_paths(report_json: dict) -> list[str]:
    """Return StaticResources-relative paths declared in resourcePackages."""

    out: list[str] = []
    for pkg in report_json.get("resourcePackages") or []:
        pkg_name = pkg.get("name") or ""
        pkg_type = pkg.get("type") or ""
        for item in pkg.get("items") or []:
            rel = item.get("path") or ""
            if not rel:
                continue
            if pkg_type == "SharedResources" or pkg_name == "SharedResources":
                out.append(f"StaticResources/SharedResources/{rel}")
            elif pkg_type == "RegisteredResources" or pkg_name == "RegisteredResources":
                out.append(f"StaticResources/RegisteredResources/{rel}")
            else:
                out.append(f"StaticResources/{pkg_name}/{rel}")
    return out


def _check_dax_refs(dax: str, catalog: ModelCatalog, where: str, errors: list[str]) -> None:
    if not dax or dax.lstrip().startswith("//"):
        return
    for m in _BARE_AGG.finditer(dax):
        errors.append(
            f"{where}: illegal bare {m.group('fn')}('{m.group('col')}') without table — "
            f"use 'Table'[{m.group('col')}]"
        )
    for m in _DAX_COL_REF.finditer(dax):
        table = (m.group("qtable") or m.group("table") or "").replace("''", "'")
        col = m.group("col")
        if not table:
            continue
        if table not in catalog.tables:
            errors.append(f"{where}: DAX references unknown table '{table}'[{col}]")
            continue
        cols = catalog.columns.get(table) or set()
        # Measures live on __Measures or any table — allow measure names as [Measure] only;
        # column must exist on the referenced table (calcs may be columns too).
        if col not in cols and col not in catalog.measures:
            # Calculated columns appear as `column 'X' =` in tmdl — already in cols.
            errors.append(f"{where}: DAX references missing column '{table}'[{col}]")


def validate_pbip_files(files: dict[str, str], *, fail: bool = True) -> list[str]:
    """Validate generated PBIP zip contents. Return error list; raise if fail and errors."""

    errors: list[str] = []
    names = set(files)

    # .platform files
    platforms = [n for n in names if n.endswith(".platform") or n.endswith("/.platform")]
    if len(platforms) < 2:
        errors.append("expected .platform in both Report and SemanticModel folders")

    # Desktop path budget (cyphou openability pattern) — forecast unzip under Downloads
    from engine.pbip.desktop_paths import check_zip_paths

    errors.extend(check_zip_paths(sorted(names)))

    # report.json resource packages → files exist
    report_paths = [n for n in names if n.endswith("/definition/report.json")]
    for rp in report_paths:
        try:
            report = json.loads(files[rp])
        except json.JSONDecodeError as exc:
            errors.append(f"{rp}: invalid JSON ({exc})")
            continue
        schema = _load_schema("fabric/item/report/definition/report/3.0.0/schema.json")
        if schema:
            _validate_required(report, schema, rp, errors)
        prefix = rp[: -len("/definition/report.json")]
        for rel in _referenced_static_paths(report):
            full = f"{prefix}/{rel}"
            if full not in names:
                errors.append(f"{rp}: resourcePackages references missing file {full}")

    # JSON schema smoke for key artifacts.
    # Top-level .pbip must NOT include "$schema" — Power BI Desktop refuses to open
    # files that have it. Validate only Desktop's keys (version, artifacts[, settings]).
    schema_map = {
        "definition.pbism": "fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "definition.pbir": "fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version.json": "fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "pages.json": "fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        ".platform": "fabric/gitIntegration/platformProperties/2.0.0/schema.json",
    }
    for path, content in files.items():
        if not path.endswith((".json", ".pbip", ".pbism", ".pbir", ".platform")):
            continue
        if path.endswith(".tmdl"):
            continue
        try:
            instance = json.loads(content)
        except json.JSONDecodeError:
            continue
        if path.endswith(".pbip"):
            if "$schema" in instance:
                errors.append(
                    f"{path}: must not include '$schema' "
                    "(Power BI Desktop silently refuses to open it)"
                )
            for key in ("version", "artifacts"):
                if key not in instance:
                    errors.append(f"{path}: missing required key '{key}'")
            continue
        for suffix, schema_rel in schema_map.items():
            if path.endswith(suffix):
                schema = _load_schema(schema_rel)
                if schema:
                    _validate_required(instance, schema, path, errors)
                break

    # Visual JSON required keys
    for path, content in files.items():
        if not path.endswith("/visual.json"):
            continue
        try:
            visual = json.loads(content)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}: invalid JSON ({exc})")
            continue
        for key in ("name", "position", "visual"):
            if key not in visual:
                errors.append(f"{path}: missing '{key}'")

    catalog = _parse_tmdl_model(files)
    for path, content in files.items():
        # Only semantic table TMDL (skip expressions.tmdl M code, relationships, etc.)
        if "/definition/tables/" not in path or not path.endswith(".tmdl"):
            continue
        # Collect multi-line measure/column expressions
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            m = re.match(
                r"^\t(?:measure|column)\s+(?:'([^']+)'|(\S+))\s*=\s*(.*)$",
                lines[i],
            )
            if not m:
                i += 1
                continue
            name = m.group(1) or m.group(2)
            expr_parts = [m.group(3).strip()]
            i += 1
            while i < len(lines) and (
                lines[i].startswith("\t\t") or (lines[i].strip() and not lines[i].startswith("\t"))
            ):
                # Stop at property lines (dataType, formatString, …)
                if re.match(
                    r"^\t\t(dataType|formatString|lineageTag|summarizeBy|displayFolder|sourceColumn|annotation)\b",
                    lines[i],
                ):
                    break
                if lines[i].startswith("\t\t") and not lines[i].startswith("\t\t\t"):
                    # indented continuation of DAX (VAR/RETURN)
                    if any(
                        lines[i].lstrip().upper().startswith(tok)
                        for tok in ("VAR ", "RETURN ", "CALCULATE", "IF(", "SWITCH")
                    ) or expr_parts[0].upper().endswith(("VAR", "RETURN")):
                        expr_parts.append(lines[i].strip())
                        i += 1
                        continue
                    break
                i += 1
            expr = " ".join(p for p in expr_parts if p)
            _check_dax_refs(expr, catalog, f"{path}:{name}", errors)

    if fail and errors:
        # De-dupe while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for e in errors:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        raise PbipValidationError(unique)
    return errors
