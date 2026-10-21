"""FIXED LOD unit + DuckDB acceptance (windows, COUNTD subquery, DAX ALL/ALLEXCEPT)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from engine import transpile, transpile_formula
from engine.audit import FIXED_SLICER_WARNING
from engine.schema import ParseStatus
from engine.transpiler import FormulaContext

HERE = Path(__file__).resolve().parent
TWB = HERE / "lod_test.twb"
CSV = HERE / "orders.csv"


def test_fixed_partition_window() -> None:
    sql, status, err = transpile_formula("{FIXED [Customer Name]: SUM([Sales])}")
    assert status == ParseStatus.ok, err
    assert sql == 'SUM("Sales") OVER (PARTITION BY "Customer Name")'
    dax, dax_st, dax_err = transpile_formula("{FIXED [Customer Name]: SUM([Sales])}", target="dax")
    assert dax_st == ParseStatus.ok, dax_err
    assert dax == (
        "CALCULATE(SUM(Orders[Sales]), ALLEXCEPT(Orders, Orders[Customer Name]))"
    )


def test_fixed_none_table_scoped_over_and_all() -> None:
    sql, status, err = transpile_formula("{SUM([Sales])}")
    assert status == ParseStatus.ok, err
    assert sql == 'SUM("Sales") OVER ()'
    dax, dax_st, dax_err = transpile_formula("{SUM([Sales])}", target="dax")
    assert dax_st == ParseStatus.ok, dax_err
    assert dax == "CALCULATE(SUM(Orders[Sales]), ALL(Orders))"
    assert "REMOVEFILTERS" not in dax


def test_fixed_countd_subquery() -> None:
    sql, status, err = transpile_formula("{FIXED [Region]: COUNTD([Customer])}")
    assert status == ParseStatus.ok, err
    assert sql == (
        '(SELECT COUNT(DISTINCT "Customer") FROM "Orders" AS _lod '
        'WHERE _lod."Region" = "Orders"."Region")'
    )
    dax, dax_st, dax_err = transpile_formula(
        "{FIXED [Region]: COUNTD([Customer])}", target="dax"
    )
    assert dax_st == ParseStatus.ok, dax_err
    assert dax == (
        "CALCULATE(DISTINCTCOUNT(Orders[Customer]), ALLEXCEPT(Orders, Orders[Region]))"
    )


def test_ratio_with_fixed_window() -> None:
    sql, status, err = transpile_formula("[Sales] / {FIXED [Customer]: SUM([Sales])}")
    assert status == ParseStatus.ok, err
    assert sql == '"Sales" / SUM("Sales") OVER (PARTITION BY "Customer")'
    dax, dax_st, dax_err = transpile_formula(
        "[Sales] / {FIXED [Customer]: SUM([Sales])}", target="dax"
    )
    assert dax_st == ParseStatus.ok, dax_err
    assert dax == (
        "DIVIDE(Orders[Sales], CALCULATE(SUM(Orders[Sales]), "
        "ALLEXCEPT(Orders, Orders[Customer])))"
    )


def test_avg_fixed_uncorrelated_group_by() -> None:
    sql, status, err = transpile_formula("AVG({FIXED [Customer]: SUM([Sales])})")
    assert status == ParseStatus.ok, err
    assert sql == (
        '(SELECT AVG(lod_inner) FROM (SELECT SUM("Sales") AS lod_inner '
        'FROM "Orders" AS _lod GROUP BY _lod."Customer") AS _lod_n)'
    )
    assert "WHERE" not in sql
    dax, dax_st, dax_err = transpile_formula(
        "AVG({FIXED [Customer]: SUM([Sales])})", target="dax"
    )
    assert dax_st == ParseStatus.ok, dax_err
    assert dax == "AVERAGEX(VALUES(Orders[Customer]), CALCULATE(SUM(Orders[Sales])))"
    assert "ALLEXCEPT" not in dax


def test_nested_fixed_skipped() -> None:
    formula = "{FIXED [Region]: AVG({FIXED [Customer Name]: SUM([Sales])})}"
    sql, status, err = transpile_formula(formula)
    assert status == ParseStatus.skipped, err
    assert sql == f"-- SKIPPED [nested-lod]: {formula}"
    dax, dax_st, dax_err = transpile_formula(formula, target="dax")
    assert dax_st == ParseStatus.skipped, dax_err
    assert dax == f"// SKIPPED [nested-lod]: {formula}"


def test_include_exclude_without_grain_skipped() -> None:
    sql, status, err = transpile_formula("{INCLUDE [Category]: SUM([Sales])}")
    assert status == ParseStatus.skipped, err
    assert "-- SKIPPED [lod-include/exclude]:" in sql
    ctx = FormulaContext(dialect="ansi", view_dims=[])
    sql2, st2, err2 = transpile_formula(
        "{EXCLUDE [Region]: SUM([Sales])}", context=ctx
    )
    assert st2 == ParseStatus.skipped, err2
    assert "-- SKIPPED [lod-include/exclude]:" in sql2
    dax, dax_st, _ = transpile_formula("{INCLUDE [Category]: SUM([Sales])}", target="dax")
    assert dax_st == ParseStatus.skipped
    assert "// SKIPPED [lod-include/exclude]:" in dax


def test_slicer_warning_in_scripts() -> None:
    result = transpile(TWB, dialect="duckdb")
    assert FIXED_SLICER_WARNING in result.sql_script
    assert f"-- WARNING: {FIXED_SLICER_WARNING}" in result.sql_script
    assert f"// WARNING: {FIXED_SLICER_WARNING}" in result.dax_script


def test_fixed_region_duckdb_values() -> None:
    result = transpile(TWB, dialect="duckdb")
    fields = {f.caption: f for f in result.calculated_fields}
    nested = fields["Nested Min Category"]
    assert nested.status == ParseStatus.skipped
    assert "-- SKIPPED [nested-lod]:" in (nested.sql or "")

    con = duckdb.connect(":memory:")
    con.execute(
        f"""
        CREATE TABLE "Orders" AS
        SELECT
          Region, Category, Customer, Sales
        FROM read_csv_auto('{CSV.as_posix()}', header=true)
        """
    )
    fixed_sql = fields["Fixed Region Sales"].sql
    countd_sql = fields["Region Customers"].sql
    rows = con.execute(
        f"""
        SELECT DISTINCT
          Region,
          ({fixed_sql}) AS fixed_sales,
          ({countd_sql}) AS customers
        FROM "Orders"
        ORDER BY Region
        """
    ).fetchall()
    expected = {"Central": (720.26, 1), "East": (175.0, 2), "West": (280.0, 1)}
    assert {r[0] for r in rows} == set(expected)
    for region, sales, customers in rows:
        exp_sales, exp_cust = expected[region]
        assert abs(sales - exp_sales) < 1e-6
        assert customers == exp_cust
