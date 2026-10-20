"""LOD14 workbook: FIXED correlation, DAX CALCULATE filters, DuckDB values."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import duckdb

from engine import transpile, transpile_formula
from engine.schema import ParseStatus
from engine.transpiler import FormulaContext

HERE = Path(__file__).resolve().parent
TWB = HERE / "lod14.twb"
CSV = HERE / "lod14_orders.csv"

EXPECTED_PCT = {"East": 0.19, "Central": 0.24, "West": 0.24, "South": 0.33}

EXPECTED_DAX = {
    "Pct of Total": "DIVIDE(SUM(Orders[Sales]), CALCULATE(SUM(Orders[Sales]), REMOVEFILTERS(Orders)))",
    "Nested Avg Customer": (
        "CALCULATE(AVERAGEX(VALUES(Orders[Customer Name]), "
        "CALCULATE(SUM(Orders[Sales]), ALLEXCEPT(Orders, Orders[Customer Name]))), "
        "ALLEXCEPT(Orders, Orders[Region]))"
    ),
    "Exclude Sub-Category Sales": (
        "CALCULATE(SUM(Orders[Sales]), REMOVEFILTERS(Orders[Sub-Category]))"
    ),
    "C01": "CALCULATE(SUM(Orders[Sales]), ALLEXCEPT(Orders, Orders[Region]))",
    "C11": "CALCULATE(SUM(Orders[Sales]), REMOVEFILTERS(Orders))",
    "First Purchase Date": (
        "CALCULATE(MIN(Orders[Order Date]), ALLEXCEPT(Orders, Orders[Customer Name]))"
    ),
    "Sales Mix": "DIVIDE(Orders[Sales], (DIVIDE([C01], [C11])))",
    "Next Month": "EDATE(Orders[Order Date], 1)",
    "Scaled Sales": "Orders[Sales] * [Multiplier Value]",
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
    "Sales Mix": "calculated_column",
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
        assert field.status == ParseStatus.ok, f"{caption}: {field.status} {field.error} {field.sql}"
        assert field.dax_status == ParseStatus.ok, f"{caption}: {field.dax_status} {field.dax}"
        assert field.dax_kind == kind, f"{caption} kind {field.dax_kind} != {kind}"
        assert "// TODO" not in (field.dax or "")
        assert "-- TODO" not in (field.sql or "")
    assert "[Multiplier Value] := 2" in result.dax_script
    assert "[Pct of Total] :=" in result.dax_script
    assert "Orders[First Purchase Date] =" in result.dax_script
    assert "/* [Parameters]" not in result.dax_script


def test_lod14_dax_exact() -> None:
    fields, _ = _fields()
    for caption, expected in EXPECTED_DAX.items():
        assert fields[caption].dax == expected, f"{caption}\n{fields[caption].dax!r}\n!=\n{expected!r}"
    assert "MAXX" not in fields["Pct of Total"].dax
    assert "REMOVEFILTERS(Orders[Sub-Category])" in fields["Exclude Sub-Category Sales"].dax


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


def test_nested_fixed_sql_correlates_own_dims() -> None:
    """Nested FIXED AVG is one value per customer (matches DAX VALUES), not row-weighted.

    Tableau can row-weight AVG when the inner LOD is repeated on fact rows.
    EscapeTableau intentionally densifies: SQL GROUP BY Customer Name then AVG,
    DAX AVERAGEX(VALUES(Customer Name), CALCULATE(SUM, ALLEXCEPT(Customer Name))).
    """

    ctx = FormulaContext(dialect="duckdb", view_dims=["Region"], source_table="Orders")
    formula = "{FIXED [Region]: AVG({FIXED [Customer Name]: SUM([Sales])})}"
    sql, status, err = transpile_formula(formula, dialect="duckdb", context=ctx)
    assert status == ParseStatus.ok, err
    assert '"Customer Name"' in sql
    assert "IN (SELECT" in sql
    assert 'GROUP BY _lod."Customer Name"' in sql
    assert '_lod."Region" = "Orders"."Region"' not in sql
    assert "AVG(lod_inner)" in sql
    dax, dax_status, dax_err = transpile_formula(formula, target="dax")
    assert dax_status == ParseStatus.ok, dax_err
    assert "AVERAGEX(VALUES(Orders[Customer Name])" in dax
    assert "ALLEXCEPT(Orders, Orders[Customer Name])" in dax
    # Not a row-weighted window over fact rows
    assert "OVER (" not in sql


def test_exclude_and_fixed_none_dax_formula() -> None:
    dax, status, err = transpile_formula("MAX({FIXED : SUM([Sales])})", target="dax")
    assert status == ParseStatus.ok, err
    assert dax == "CALCULATE(SUM(Orders[Sales]), REMOVEFILTERS(Orders))"
    nested, nst, nerr = transpile_formula(
        "{FIXED [Region]: AVG({FIXED [Customer Name]: SUM([Sales])})}",
        target="dax",
    )
    assert nst == ParseStatus.ok, nerr
    assert nested == EXPECTED_DAX["Nested Avg Customer"]
    exc, est, eerr = transpile_formula(
        "SUM({EXCLUDE [Sub-Category]: SUM([Sales])})",
        target="dax",
    )
    assert est == ParseStatus.ok, eerr
    assert exc == EXPECTED_DAX["Exclude Sub-Category Sales"]


def _customer_totals(rows: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        totals[row["Customer Name"]] += float(row["Sales"])
    return totals


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
    raw = con.execute(
        'SELECT Region, Category, "Customer Name", Sales FROM "Orders"'
    ).fetchall()
    source = [
        {"Region": r, "Category": c, "Customer Name": n, "Sales": s}
        for r, c, n, s in raw
    ]
    cust_tot = _customer_totals(source)
    region_custs: dict[str, set[str]] = defaultdict(set)
    region_sales: dict[str, float] = defaultdict(float)
    for row in source:
        region_custs[row["Region"]].add(row["Customer Name"])
        region_sales[row["Region"]] += float(row["Sales"])
    grand = sum(region_sales.values())
    expected_nested = {
        region: sum(cust_tot[c] for c in names) / len(names)
        for region, names in region_custs.items()
    }

    pct_sql = fields["Pct of Total"].sql
    nested_sql = fields["Nested Avg Customer"].sql
    rows = con.execute(
        f"""
        SELECT
          Region,
          {pct_sql} AS pct,
          MIN(({nested_sql})) AS nested_avg
        FROM "Orders"
        GROUP BY Region
        ORDER BY Region
        """
    ).fetchall()
    assert {r[0] for r in rows} == set(EXPECTED_PCT)
    for region, pct, nested in rows:
        assert abs(pct - EXPECTED_PCT[region]) < 1e-9, f"{region} pct {pct}"
        assert abs(pct - 1.0) > 1e-6
        assert abs(nested - expected_nested[region]) < 1e-6, f"{region} nested {nested}"
        include_only = region_sales[region] / len(region_custs[region])
        if abs(include_only - expected_nested[region]) > 1e-6:
            assert abs(nested - include_only) > 1e-6
    assert abs(grand - 10000) < 1e-6
