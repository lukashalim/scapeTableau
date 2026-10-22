"""Stage 1 PBIP zip shape + TMDL parse + CSV / Postgres / Superstore fixtures."""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from engine import transpile
from engine.pbip import build_pbip_zip, transpile_to_pbip
from engine.pbip.m_partitions import plan_m_partition
from engine.pbip.render import load_template
from engine.schema import ConnectionInfo, DatasourceInfo, ParseStatus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pbip"
SAMPLES = ROOT / "samples"
SUPERSTORE = Path("/cursor/stores/self/internal/US_Superstore_10.0.twbx")
if not SUPERSTORE.is_file():
    SUPERSTORE = SAMPLES / "US_Superstore_10.0.twbx"


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return set(zf.namelist())


def _read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return zf.read(name).decode("utf-8")


def test_templates_exist() -> None:
    assert "compatibilityLevel" in load_template("database.tmdl")
    assert "datasetReference" in load_template("definition.pbir.json")


def test_csv_twb_pbip_shape() -> None:
    zip_bytes, result = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    names = _zip_names(zip_bytes)
    # Find project name prefix
    pbip = next(n for n in names if n.endswith(".pbip"))
    prefix = pbip[: -len(".pbip")]
    sm = f"{prefix}.SemanticModel"
    report = f"{prefix}.Report"
    required = {
        pbip,
        f"{sm}/definition.pbism",
        f"{sm}/definition/database.tmdl",
        f"{sm}/definition/model.tmdl",
        f"{sm}/definition/relationships.tmdl",
        f"{sm}/definition/expressions.tmdl",
        f"{report}/definition.pbir",
        f"{report}/definition/version.json",
        f"{report}/definition/report.json",
        f"{report}/definition/pages/pages.json",
        "CONVERSION_REPORT.md",
    }
    assert required <= names
    assert any(n.startswith(f"{sm}/definition/tables/") and n.endswith(".tmdl") for n in names)

    pbir = json.loads(_read(zip_bytes, f"{report}/definition.pbir"))
    assert pbir["datasetReference"]["byPath"]["path"] == f"../{sm}"

    model = _read(zip_bytes, f"{sm}/definition/model.tmdl")
    assert "ref table" in model
    # Small CSVs are embedded in M (Custom SQL / Postgres must not stay live)
    tables = [n for n in names if "/tables/" in n and n.endswith(".tmdl")]
    blob = "\n".join(_read(zip_bytes, n) for n in tables)
    assert "Text.ToBinary" in blob
    assert "Csv.Document" in blob
    assert "PostgreSQL.Database" not in blob
    assert "DataFolder" not in blob
    assert "CONVERSION_REPORT.md" in names
    report_md = _read(zip_bytes, "CONVERSION_REPORT.md")
    assert "converted" in report_md.lower()
    assert result.calculated_fields


def test_postgres_custom_sql_uses_datafolder_csv() -> None:
    """Desktop-open PBIP must not prompt for Postgres on West Custom SQL."""
    result = transpile(SAMPLES / "superstore_calcs.twb")
    west = next(ds for ds in result.datasources if (ds.caption or "") == "West Custom SQL")
    assert any((c.class_name or "").lower() == "postgres" for c in west.connections)
    assert any(r.custom_sql for r in west.relations)
    zip_bytes = build_pbip_zip(result, source=SAMPLES / "superstore_calcs.twb")
    names = _zip_names(zip_bytes)
    blob = b"".join(
        zipfile.ZipFile(BytesIO(zip_bytes)).read(n)
        for n in names
        if n.endswith(".tmdl")
    ).decode("utf-8", errors="replace")
    assert "PostgreSQL.Database" not in blob
    assert "Text.ToBinary" in blob
    assert "West,100,20" in blob
    west_csv = next(n for n in names if n.endswith("West Custom SQL.csv"))
    west_body = zipfile.ZipFile(BytesIO(zip_bytes)).read(west_csv).decode("utf-8-sig")
    assert "West" in west_body
    assert "Profit" in west_body.splitlines()[0] or "Sales" in west_body.splitlines()[0]


def _orders_ds() -> DatasourceInfo:
    return DatasourceInfo(
        name="Orders",
        caption="Orders",
        connections=[ConnectionInfo(class_name="textscan", filename="Orders.csv")],
    )


def test_small_csv_is_embedded_not_postgres() -> None:
    plan = plan_m_partition(
        _orders_ds(),
        table_name="Orders",
        column_types=[("Region", "type text"), ("Sales", "type number")],
        csv_filename="Orders.csv",
        csv_text='Region,Sales\nWest,"1,000"\n',
    )
    assert "Text.ToBinary" in plan.expression
    assert "PostgreSQL.Database" not in plan.expression
    assert "DataFolder" not in plan.expression
    assert 'West,""1,000""' in plan.expression
    assert plan.parameters == []


def test_large_csv_uses_datafolder() -> None:
    plan = plan_m_partition(
        _orders_ds(),
        table_name="Orders",
        column_types=[("Region", "type text")],
        csv_filename="Orders.csv",
        csv_text="Region\n" + ("West\n" * 40),
        inline_max_chars=20,
    )
    assert "DataFolder" in plan.expression
    assert "Text.ToBinary" not in plan.expression
    assert "PostgreSQL.Database" not in plan.expression


def test_join_fixture_relationships() -> None:
    zip_bytes, result = transpile_to_pbip(FIXTURES / "join_demo.twb")
    assert result.joins, "expected join parse from fixture"
    rel_path = next(n for n in _zip_names(zip_bytes) if n.endswith("relationships.tmdl"))
    rel = _read(zip_bytes, rel_path)
    assert "relationship" in rel
    assert "fromCardinality: many" in rel
    assert "toCardinality: one" in rel
    assert "crossFilteringBehavior: oneDirection" in rel
    # No leading empty line (Desktop: InvalidLineType Empty!)
    assert not rel.startswith("\n")
    assert "///" not in rel


def test_empty_relationships_tmdl_is_truly_empty() -> None:
    """Desktop rejects relationships.tmdl with comment + blank line (Empty! at line 2)."""
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    rel_path = next(n for n in _zip_names(zip_bytes) if n.endswith("relationships.tmdl"))
    rel = _read(zip_bytes, rel_path)
    assert rel == "", repr(rel)


def test_empty_expressions_tmdl_is_truly_empty() -> None:
    """Desktop rejects expressions.tmdl with a comment and a blank line 2."""
    zip_bytes, _ = transpile_to_pbip(FIXTURES / "join_demo.twb")
    expr_path = next(n for n in _zip_names(zip_bytes) if n.endswith("expressions.tmdl"))
    expr = _read(zip_bytes, expr_path)
    assert expr == "", repr(expr)


@pytest.mark.skipif(not SUPERSTORE.is_file(), reason="US_Superstore_10.0.twbx missing")
def test_superstore_pbip_golden_shape() -> None:
    zip_bytes, result = transpile_to_pbip(SUPERSTORE)
    names = _zip_names(zip_bytes)
    assert "CONVERSION_REPORT.md" in names
    assert any(n.endswith(".pbip") for n in names)
    # Hyper → CSV placeholder TODO
    report = _read(zip_bytes, "CONVERSION_REPORT.md")
    assert "Hyper" in report or "hyper" in report.lower()
    captions = {f.caption: f for f in result.calculated_fields}
    assert "Profit Ratio" in captions
    assert captions["Profit Ratio"].dax_status == ParseStatus.ok
    assert "DIVIDE(" in (captions["Profit Ratio"].dax or "")
    # Measures table contains Profit Ratio
    measures_file = next(n for n in names if n.endswith("__Measures.tmdl"))
    measures = _read(zip_bytes, measures_file)
    assert "Profit Ratio" in measures
    assert "Profit per Customer" in measures
    assert "Achieved Quota" in measures or any(
        f.caption == "Achieved Quota" and f.dax_kind == "measure" for f in result.calculated_fields
    )


_TMDL_OBJECT = re.compile(r"^(table|column|measure|partition|relationship|expression|ref|database|model)\b", re.M)


def test_tmdl_parse_smoke() -> None:
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    for name in _zip_names(zip_bytes):
        if not name.endswith(".tmdl"):
            continue
        text = _read(zip_bytes, name)
        # Minimal structural parse: objects present, no unresolved placeholders
        assert "${" not in text  # unresolved template placeholders
        # relationships.tmdl may be truly empty (no joins) — Desktop requires that
        if name.endswith("relationships.tmdl") and text == "":
            continue
        assert _TMDL_OBJECT.search(text) or text.strip().startswith("///")
