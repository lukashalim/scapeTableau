"""DuckDB acceptance for the 14 lod_test.twb calculations (Sheet 1 Region grain)."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import transpile
from engine.schema import ParseStatus

HERE = Path(__file__).resolve().parent
TWB = HERE / "lod_test.twb"
CSV = HERE / "orders.csv"
EXPECTED_CAPTIONS = [
    "Fixed Region Sales",
    "Grand Total Sales",
    "Include Category Sales",
    "Avg Customer Sales",
    "Exclude Region Sales",
    "Region Customers",
    "Nested Min Category",
    "Region Share",
    "Region Case",
    "Sales Band",
    "Scaled Sales",
    "Order Year",
    "Ship Days",
    "Order Month",
    "Next Month",
]


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        f"""
        CREATE TABLE "Orders" AS
        SELECT
          Region,
          Category,
          Customer,
          Sales,
          Profit,
          CAST("Order Date" AS DATE) AS "Order Date",
          CAST("Ship Date" AS DATE) AS "Ship Date"
        FROM read_csv_auto('{CSV.as_posix()}', header=true)
        """
    )
    return con


def _field_map(dialect: str = "duckdb"):
    result = transpile(TWB, dialect=dialect)
    return {f.caption: f for f in result.calculated_fields}, result


def test_lod_fourteen_calcs_ok() -> None:
    fields, result = _field_map()
    assert [f.caption for f in result.calculated_fields if f.caption in EXPECTED_CAPTIONS]
    for caption in EXPECTED_CAPTIONS:
        field = fields[caption]
        if caption == "Nested Min Category":
            assert field.status == ParseStatus.skipped, f"{caption}: {field.status}"
            assert "-- SKIPPED [nested-lod]:" in (field.sql or "")
            continue
        assert field.status == ParseStatus.ok, f"{caption}: {field.status} {field.error} {field.sql}"
        assert field.sql
        assert "{{ config" not in result.sql_script
        assert '"Orders"' in result.sql_script
        assert '"source"' not in result.sql_script


def test_lod_duckdb_matches_expected() -> None:
    fields, _result = _field_map()
    con = _connect()
    runnable = [c for c in EXPECTED_CAPTIONS if c != "Nested Min Category"]
    assert fields["Nested Min Category"].status == ParseStatus.skipped
    exprs = {caption: fields[caption].sql for caption in runnable}
    select_list = ",\n          ".join(f'({exprs[c]}) AS "{c}"' for c in runnable)
    rows = con.execute(
        f"""
        SELECT
          Region,
          Category,
          Customer,
          Sales,
          {select_list}
        FROM "Orders"
        ORDER BY Region, Category, Customer, Sales
        """
    ).fetchall()
    cols = [
        "Region",
        "Category",
        "Customer",
        "Sales",
        *runnable,
    ]
    got = [dict(zip(cols, row, strict=True)) for row in rows]

    region_sales = {"East": 175.0, "West": 280.0, "Central": 720.26}
    grand = 1175.26
    include = {
        "East": {"Furniture": 125.0, "Office Supplies": 50.0},
        "West": {"Furniture": 200.0, "Office Supplies": 80.0},
        "Central": {"Furniture": 720.26},
    }
    avg_customer = {"East": 87.5, "West": 280.0, "Central": 720.26}
    region_customers = {"East": 2, "West": 1, "Central": 1}
    region_case = {"East": 1, "West": 2, "Central": 0}
    saw_central = False
    for row in got:
        region = row["Region"]
        category = row["Category"]
        if region == "Central":
            saw_central = True
        assert row["Fixed Region Sales"] == region_sales[region]
        assert abs(row["Grand Total Sales"] - grand) < 1e-9
        assert row["Include Category Sales"] == include[region][category]
        got_avg = row["Avg Customer Sales"]
        if region == "Central":
            assert abs(got_avg - 720.26) < 0.01
        else:
            assert got_avg == avg_customer[region]
        assert abs(row["Exclude Region Sales"] - grand) < 1e-9
        assert row["Region Customers"] == region_customers[region]
        share = region_sales[region] / grand
        assert abs(row["Region Share"] - share) < 1e-9
        assert row["Region Case"] == region_case[region]
        assert row["Sales Band"] == ("High" if row["Sales"] > 100 else "Low")
        assert row["Scaled Sales"] == row["Sales"] * 2
        assert row["Order Year"] == 2020
    assert saw_central, "orders.csv must include Central rows for the 720.26 check"

    ship = con.execute(
        """
        SELECT DATE_DIFF('day', "Order Date", "Ship Date") FROM "Orders"
        ORDER BY Region, Category, Customer, Sales
        """
    ).fetchall()
    months = con.execute(
        """
        SELECT DATE_TRUNC('month', "Order Date"),
               "Order Date" + 1 * INTERVAL '1 month'
        FROM "Orders"
        ORDER BY Region, Category, Customer, Sales
        """
    ).fetchall()
    for row, (days,), (trunc, nxt) in zip(got, ship, months, strict=True):
        assert row["Ship Days"] == days
        assert row["Order Month"] == trunc
        assert row["Next Month"] == nxt


def test_include_exclude_region_grain() -> None:
    fields, _ = _field_map()
    inc = fields["Include Category Sales"].sql
    exc = fields["Exclude Region Sales"].sql
    assert "PARTITION BY" in inc
    assert '"Region"' in inc and '"Category"' in inc
    assert "OVER ()" in exc
    assert "Review LOD logic" not in inc
    assert "Review LOD logic" not in exc
    avg = fields["Avg Customer Sales"].sql
    assert "GROUP BY" in avg and "AVG(lod_inner)" in avg
    assert "OVER (" not in avg


def main() -> None:
    test_lod_fourteen_calcs_ok()
    test_lod_duckdb_matches_expected()
    test_include_exclude_region_grain()
    print("lod_test.twb: 14/14 calcs matched DuckDB expectations")


if __name__ == "__main__":
    main()
