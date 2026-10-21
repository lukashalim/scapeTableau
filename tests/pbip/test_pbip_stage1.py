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
from engine.pbip.render import load_template
from engine.schema import ParseStatus

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
        f"{report}/report.json",
        "CONVERSION_REPORT.md",
    }
    assert required <= names
    assert any(n.startswith(f"{sm}/definition/tables/") and n.endswith(".tmdl") for n in names)

    pbir = json.loads(_read(zip_bytes, f"{report}/definition.pbir"))
    assert pbir["datasetReference"]["byPath"]["path"] == f"../{sm}"

    model = _read(zip_bytes, f"{sm}/definition/model.tmdl")
    assert "ref table" in model
    # CSV File.Contents partition
    tables = [n for n in names if "/tables/" in n and n.endswith(".tmdl")]
    blob = "\n".join(_read(zip_bytes, n) for n in tables)
    assert "File.Contents" in blob or "Csv.Document" in blob
    assert "PostgreSQL.Database" in blob or "Value.NativeQuery" in blob
    assert "CONVERSION_REPORT.md" in names
    report_md = _read(zip_bytes, "CONVERSION_REPORT.md")
    assert "converted" in report_md.lower()
    assert result.calculated_fields


def test_postgres_live_connection_fixture() -> None:
    result = transpile(SAMPLES / "superstore_calcs.twb")
    west = next(ds for ds in result.datasources if (ds.caption or "") == "West Custom SQL")
    assert any((c.class_name or "").lower() == "postgres" for c in west.connections)
    assert any(r.custom_sql for r in west.relations)
    zip_bytes = build_pbip_zip(result)
    blob = b"".join(
        zipfile.ZipFile(BytesIO(zip_bytes)).read(n)
        for n in _zip_names(zip_bytes)
        if n.endswith(".tmdl")
    ).decode("utf-8", errors="replace")
    assert "PostgreSQL.Database" in blob or "NativeQuery" in blob


def test_join_fixture_relationships() -> None:
    zip_bytes, result = transpile_to_pbip(FIXTURES / "join_demo.twb")
    assert result.joins, "expected join parse from fixture"
    rel_path = next(n for n in _zip_names(zip_bytes) if n.endswith("relationships.tmdl"))
    rel = _read(zip_bytes, rel_path)
    assert "relationship" in rel
    assert "fromCardinality: many" in rel
    assert "toCardinality: one" in rel
    assert "crossFilteringBehavior: oneDirection" in rel


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
        assert _TMDL_OBJECT.search(text) or text.strip().startswith("///")
