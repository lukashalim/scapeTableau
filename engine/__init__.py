"""EscapeTableau conversion engine: parse Tableau workbooks and emit ANSI SQL / DAX."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import BinaryIO

from engine.audit import assemble_result
from engine.extract import build_inventory, extract_to_xlsx
from engine.inspect import build_inspect
from engine.parser import parse_workbook
from engine.schema import (
    CalculatedField,
    ParameterInfo,
    ParseStatus,
    TranspileResult,
    WorkbookParseError,
)
from engine.transpiler import (
    FormulaContext,
    classify_dax_kind,
    field_references,
    normalize_table_name,
    parse_formula,
    resolve_internal_names,
    transpile_formula,
    transpile_tree,
    view_dims_for_calc,
)

__all__ = [
    "transpile",
    "transpile_formula",
    "extract_to_xlsx",
    "TranspileResult",
    "WorkbookParseError",
    "ParseStatus",
    "CalculatedField",
]


def _strip_brackets(name: str) -> str:
    text = name.strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1]
    return text


def _param_index(params: list[ParameterInfo]) -> dict[str, ParameterInfo]:
    index: dict[str, ParameterInfo] = {}
    for param in params:
        for key in (param.caption, param.name, _strip_brackets(param.name), _strip_brackets(param.caption)):
            if key:
                index[key] = param
    return index


def _source_tables(workbook, datasource_name: str) -> tuple[str, str]:
    """Return (sql_table, dax_table). DAX uses the relation name when present."""

    for ds in workbook.datasources:
        if ds.name == datasource_name or (ds.caption or "") == datasource_name:
            for rel in ds.relations:
                sql_table = normalize_table_name(rel.table) or normalize_table_name(rel.name)
                dax_table = normalize_table_name(rel.name) or sql_table
                if sql_table or dax_table:
                    return sql_table or "Orders", dax_table or "Orders"
            caption = normalize_table_name(ds.caption) or normalize_table_name(ds.name) or "Orders"
            return caption, caption
    return "Orders", "Orders"


def _calc_keys(name: str, caption: str) -> list[str]:
    keys = [_strip_brackets(name), caption, name]
    return [k for k in keys if k]


def transpile(
    source: str | Path | bytes | BinaryIO,
    *,
    dialect: str = "ansi",
    target: str = "sql",
    script_style: str = "sql",
) -> TranspileResult:
    """Parse a .twb/.twbx (path, bytes, or file object) and transpile calculations."""

    dialect_l = dialect.lower()
    target_l = "dax" if dialect_l == "dax" or target.lower() == "dax" else "sql"
    sql_dialect = "ansi" if dialect_l == "dax" else dialect_l

    workbook = parse_workbook(source)
    params = _param_index(workbook.parameters)

    groups: dict[str, list] = defaultdict(list)
    for raw in workbook.calculated_fields:
        groups[raw.datasource_name].append(raw)

    fields: list[CalculatedField] = []
    for ds_name, raws in groups.items():
        name_map = workbook.name_maps.get(ds_name, {})
        key_to_raw: dict[str, object] = {}
        deps: dict[str, set[str]] = {}
        identity: dict[str, str] = {}
        for raw in raws:
            ident = raw.caption or raw.name
            resolved = resolve_internal_names(raw.formula, name_map)
            refs = field_references(resolved)
            raw_keys = _calc_keys(raw.name, raw.caption)
            for key in raw_keys:
                key_to_raw[key] = raw
                identity[key] = ident
            deps[ident] = set()
            for ref in refs:
                caption = name_map.get(ref) or name_map.get(f"[{ref}]") or ref
                if caption in {raw.caption, _strip_brackets(raw.name)}:
                    continue
                if caption in {r.caption for r in raws} or ref in key_to_raw or any(
                    _strip_brackets(r.name) == ref or r.caption == ref for r in raws
                ):
                    target_ident = None
                    for r in raws:
                        if r.caption == ref or _strip_brackets(r.name) == ref or r.caption == caption:
                            target_ident = r.caption or r.name
                            break
                    if target_ident and target_ident != ident:
                        deps[ident].add(target_ident)

        indeg: dict[str, int] = {raw.caption or raw.name: 0 for raw in raws}
        incoming: dict[str, set[str]] = {k: set(v) for k, v in deps.items()}
        for ident, ds in incoming.items():
            indeg[ident] = len(ds)
        queue = deque([k for k, n in indeg.items() if n == 0])
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for other, ds in incoming.items():
                if node in ds:
                    ds.remove(node)
                    indeg[other] -= 1
                    if indeg[other] == 0:
                        queue.append(other)
        leftover = [raw.caption or raw.name for raw in raws if (raw.caption or raw.name) not in order]
        order.extend(leftover)
        raw_by_ident = {raw.caption or raw.name: raw for raw in raws}

        calc_sql: dict[str, str] = {}
        calc_dax: dict[str, str] = {}
        calc_status: dict[str, ParseStatus] = {}
        calc_dax_status: dict[str, ParseStatus] = {}
        calc_dax_kind: dict[str, str] = {}
        table, dax_table = _source_tables(workbook, ds_name)

        for ident in order:
            raw = raw_by_ident[ident]
            resolved = resolve_internal_names(raw.formula, name_map)
            grain = view_dims_for_calc(raw.name, raw.caption, workbook.worksheets)
            current_keys = set(_calc_keys(raw.name, raw.caption))
            sql_failed_dep = None
            dax_failed_dep = None
            for dep in deps.get(ident, set()):
                st = calc_status.get(dep)
                if st is not None and st != ParseStatus.ok and sql_failed_dep is None:
                    sql_failed_dep = dep
                dst = calc_dax_status.get(dep)
                if dst is not None and dst != ParseStatus.ok and dax_failed_dep is None:
                    dax_failed_dep = dep

            ctx_sql = FormulaContext(
                dialect=sql_dialect,
                target="sql",
                parameters=params,
                calc_sql=calc_sql,
                calc_dax=calc_dax,
                calc_status=calc_status,
                calc_dax_status=calc_dax_status,
                calc_dax_kind=calc_dax_kind,
                view_dims=grain,
                source_table=table,
                dax_table=dax_table,
                current_keys=current_keys,
                original_formula=resolved,
            )
            ctx_dax = FormulaContext(
                dialect=sql_dialect,
                target="dax",
                parameters=params,
                calc_sql=calc_sql,
                calc_dax=calc_dax,
                calc_status=calc_status,
                calc_dax_status=calc_dax_status,
                calc_dax_kind=calc_dax_kind,
                view_dims=grain,
                source_table=table,
                dax_table=dax_table,
                current_keys=current_keys,
                original_formula=resolved,
            )

            try:
                tree = parse_formula(resolved, dialect=sql_dialect)
            except Exception:
                tree = None

            if sql_failed_dep is not None:
                msg = f"Dependency failed: {sql_failed_dep}"
                sql = f"-- TODO: {msg}: {resolved}"
                sql_status, sql_err = ParseStatus.error, msg
            elif tree is not None:
                sql, sql_status, sql_err, _ = transpile_tree(tree, ctx=ctx_sql)
            else:
                sql, sql_status, sql_err = transpile_formula(
                    resolved, dialect=sql_dialect, target="sql", context=ctx_sql
                )

            if dax_failed_dep is not None:
                msg = f"Dependency failed: {dax_failed_dep}"
                dax = f"// TODO: {msg}: {resolved}"
                dax_status, dax_err = ParseStatus.error, msg
            elif tree is not None:
                dax, dax_status, dax_err, _ = transpile_tree(tree, ctx=ctx_dax)
            else:
                dax, dax_status, dax_err = transpile_formula(
                    resolved, dialect=sql_dialect, target="dax", context=ctx_dax
                )

            kind = "measure"
            if tree is not None:
                kind = classify_dax_kind(tree, ctx_dax)
            status = sql_status
            error = sql_err
            if target_l == "dax":
                status = dax_status
                error = dax_err
            for key in current_keys:
                calc_sql[key] = sql
                calc_dax[key] = dax
                calc_status[key] = sql_status
                calc_dax_status[key] = dax_status
                calc_dax_kind[key] = kind

            fields.append(
                CalculatedField(
                    name=raw.name,
                    caption=raw.caption,
                    datatype=raw.datatype,
                    role=raw.role,
                    formula=raw.formula,
                    sql=sql,
                    status=status,
                    datasource=raw.datasource_caption or raw.datasource_name,
                    error=error,
                    dax=dax,
                    dax_status=dax_status,
                    dax_kind=kind,
                )
            )

    inspect = build_inspect(workbook, fields)
    inventory = build_inventory(workbook=workbook)
    return assemble_result(
        fields,
        workbook.datasources,
        workbook_name=workbook.workbook_name,
        dialect=sql_dialect if target_l != "dax" else "dax",
        inspect=inspect,
        extract_inventory=inventory,
        dashboards=inspect.dashboards,
        target=target_l,
        script_style=script_style,
        parameters=workbook.parameters,
    )
