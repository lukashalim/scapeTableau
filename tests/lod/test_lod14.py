"""LOD14 workbook: FIXED correlation, DAX CALCULATE filters, DuckDB values."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from engine import transpile, transpile_formula
from engine.schema import ParseStatus
from engine.transpiler import FormulaContext

HERE = Path(__file__).resolve().parent
TWB = HERE / "lod14.twb"
CSV = HERE / "lod14_orders.csv"

EXPECTED_PCT = {"East": 0.19, "Central": 0.24, "West": 0.24, "South": 0.33}

NESTED_CAPTIONS = {"Nested Avg Customer", "Nested Min Category"}

EXPECTED_DAX = {
    "Pct of Total": "DIVIDE(SUM('Orders'[Sales]), CALCULATE(SUM('Orders'[Sales]), ALL('Orders')))",
    "Exclude Sub-Category Sales": (
        "CALCULATE(SUM('Orders'[Sales]), REMOVEFILTERS('Orders'[Sub-Category]))"
    ),
    "C01": "CALCULATE(SUM('Orders'[Sales]), ALLEXCEPT('Orders', 'Orders'[Region]))",
    "C11": "CALCULATE(SUM('Orders'[Sales]), ALL('Orders'))",
    "First Purchase Date": (
        "CALCULATE(MIN('Orders'[Order Date]), ALLEXCEPT('Orders', 'Orders'[Customer Name]))"
    ),
    "Sales Mix": "DIVIDE('Orders'[Sales], (DIVIDE([C01], [C11])))",
    "Next Month": "EDATE('Orders'[Order Date], 1)",
    "Scaled Sales": "'Orders'[Sales] * [Multiplier Value]",
}

EXPECTED_KIND = {
    "C01": "measure",
    "C11": "measure",
    "Pct of Total": "measure",
    "Nested Avg Customer": "measure",
    "Avg Customer Include": "measure",
    "Exclude Sub-Category Sales": "measure",
    "Include Category Sales": "measure",
    "Region Customers": "measure",
    "Nested Min Category": "measure",
    "First Purchase Date": "calculated_column",
    "Days Since First Purchase": "calculated_column",
    "Sales Mix": "measure",
    "Scaled Sales": "calculated_column",
    "Next Month": "calculated_column",
}


def _fields():
    result = transpile(TWB, dialect="duckdb")
    return {f.caption: f for f in result.calculated_fields}, result


def test_lod14_fourteen_calcs() -> None:
    fields, result = _fields()
    assert set(EXPECTED_KIND) <= set(fields)
    assert len(EXPECTED_KIND) == 14
    for caption, kind in EXPECTED_KIND.items():
        field = fields[caption]
        if caption in NESTED_CAPTIONS:
            assert field.status == ParseStatus.skipped, f"{caption}: {field.status}"
            assert field.dax_status == ParseStatus.skipped
            assert "-- SKIPPED [nested-lod]:" in (field.sql or "")
            assert "// SKIPPED [nested-lod]:" in (field.dax or "")
            continue
        assert field.status == ParseStatus.ok, f"{caption}: {field.status} {field.error} {field.sql}"
        assert field.dax_status == ParseStatus.ok, f"{caption}: {field.dax_status} {field.dax}"
        assert field.dax_kind == kind, f"{caption} kind {field.dax_kind} != {kind}"
        assert "// TODO" not in (field.dax or "")
        assert "-- TODO" not in (field.sql or "")
    assert "[Multiplier Value] := 2" in result.dax_script
    assert "[Pct of Total] :=" in result.dax_script
    assert "'Orders'[First Purchase Date] =" in result.dax_script
    assert "/* [Parameters]" not in result.dax_script


def test_lod14_dax_exact() -> None:
    fields, _ = _fields()
    for caption, expected in EXPECTED_DAX.items():
        assert fields[caption].dax == expected, f"{caption}\n{fields[caption].dax!r}\n!=\n{expected!r}"
    assert "MAXX" not in fields["Pct of Total"].dax
    assert "REMOVEFILTERS('Orders'[Sub-Category])" in fields["Exclude Sub-Category Sales"].dax
    for caption in NESTED_CAPTIONS:
        assert fields[caption].status == ParseStatus.skipped


def test_fixed_none_sql_uncorrelated() -> None:
    ctx = FormulaContext(dialect="duckdb", view_dims=["Region"], source_table="Orders")
    sql, status, err = transpile_formula(
        "SUM([Sales]) / MAX({FIXED : SUM([Sales])})",
        dialect="duckdb",
        context=ctx,
    )
    assert status == ParseStatus.ok, err
    assert 'SELECT SUM("Sales") FROM "Orders"' in sql
    assert "WHERE" not in sql


@pytest.mark.skip(reason="Nested LOD skipped by FIXED v1 spec [nested-lod]")
def test_nested_fixed_sql_correlates_own_dims() -> None:
    """Historical nested FIXED densify path — superseded by SKIPPED [nested-lod]."""

    ctx = FormulaContext(dialect="duckdb", view_dims=["Region"], source_table="Orders")
    formula = "{FIXED [Region]: AVG({FIXED [Customer Name]: SUM([Sales])})}"
    sql, status, err = transpile_formula(formula, dialect="duckdb", context=ctx)
    assert status == ParseStatus.ok, err


@pytest.mark.skip(reason="Nested LOD DuckDB values superseded by SKIPPED [nested-lod]")
def test_nested_avg_customer_duckdb_values() -> None:
    """Was part of lod14 nested densify checks; nested FIXED is now skipped."""

    assert False, "unreachable"


@pytest.mark.skip(reason="Nested Min Category DuckDB superseded by SKIPPED [nested-lod]")
def test_nested_min_category_duckdb_values() -> None:
    """Was part of lod_test nested densify checks; nested FIXED is now skipped."""

    assert False, "unreachable"


def test_exclude_and_fixed_none_dax_formula() -> None:
    dax, status, err = transpile_formula("MAX({FIXED : SUM([Sales])})", target="dax")
    assert status == ParseStatus.ok, err
    assert dax == "CALCULATE(SUM('Orders'[Sales]), ALL('Orders'))"
    nested, nst, nerr = transpile_formula(
        "{FIXED [Region]: AVG({FIXED [Customer Name]: SUM([Sales])})}",
        target="dax",
    )
    assert nst == ParseStatus.skipped, nerr
    assert "// SKIPPED [nested-lod]:" in nested
    exc, est, eerr = transpile_formula(
        "SUM({EXCLUDE [Sub-Category]: SUM([Sales])})",
        target="dax",
        context=FormulaContext(view_dims=["Region", "Sub-Category"]),
    )
    assert est == ParseStatus.ok, eerr
    assert exc == EXPECTED_DAX["Exclude Sub-Category Sales"]


def test_lod14_duckdb_pct_and_nested() -> None:
    fields, _ = _fields()
    con = duckdb.connect(":memory:")
    con.execute(
        f"""
        CREATE TABLE "Orders" AS
        SELECT
          Region,
          Category,
          "Sub-Category",
          "Customer Name",
          Sales,
          CAST("Order Date" AS DATE) AS "Order Date"
        FROM read_csv_auto('{CSV.as_posix()}', header=true)
        """
    )
    pct_sql = fields["Pct of Total"].sql
    rows = con.execute(
        f"""
        SELECT
          Region,
          {pct_sql} AS pct
        FROM "Orders"
        GROUP BY Region
        ORDER BY Region
        """
    ).fetchall()
    assert {r[0] for r in rows} == set(EXPECTED_PCT)
    for region, pct in rows:
        assert abs(pct - EXPECTED_PCT[region]) < 1e-9, f"{region} pct {pct}"
        assert abs(pct - 1.0) > 1e-6
    for caption in NESTED_CAPTIONS:
        assert fields[caption].status == ParseStatus.skipped
    grand = con.execute('SELECT SUM(Sales) FROM "Orders"').fetchone()[0]
    assert abs(grand - 10000) < 1e-6
