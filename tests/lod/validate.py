"""DuckDB acceptance for the 14 lod_test.twb calculations (Sheet 1 Region grain)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from engine import transpile
from engine.schema import ParseStatus

HERE = Path(__file__).resolve().parent
TWB = HERE / "lod_test.twb"
CSV = HERE / "orders.csv"
EXPECTED_CAPTIONS = [
    "Fixed Region Sales",
    "Grand Total Sales",
    "Include Category Sales",
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
        assert field.status == ParseStatus.ok, f"{caption}: {field.status} {field.error} {field.sql}"
        assert field.sql
        assert "{{ config" not in result.sql_script
        assert '"Orders"' in result.sql_script
        assert '"source"' not in result.sql_script


def test_lod_duckdb_matches_expected() -> None:
    fields, _result = _field_map()
    con = _connect()
    exprs = {caption: fields[caption].sql for caption in EXPECTED_CAPTIONS}
    select_list = ",\n          ".join(f'({exprs[c]}) AS "{c}"' for c in EXPECTED_CAPTIONS)
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
        *EXPECTED_CAPTIONS,
    ]
    got = [dict(zip(cols, row, strict=True)) for row in rows]

    east_sales, west_sales = 175.0, 280.0
    grand = 455.0
    for row in got:
        region = row["Region"]
        category = row["Category"]
        assert row["Fixed Region Sales"] == (east_sales if region == "East" else west_sales)
        assert row["Grand Total Sales"] == grand
        include = {"East": {"Furniture": 125.0, "Office Supplies": 50.0}, "West": {"Furniture": 200.0, "Office Supplies": 80.0}}
        assert row["Include Category Sales"] == include[region][category]
        assert row["Exclude Region Sales"] == grand
        assert row["Region Customers"] == (2 if region == "East" else 1)
        assert row["Nested Min Category"] == (50.0 if region == "East" else 80.0)
        share = (east_sales if region == "East" else west_sales) / grand
        assert abs(row["Region Share"] - share) < 1e-9
        assert row["Region Case"] == (1 if region == "East" else 2)
        assert row["Sales Band"] == ("High" if row["Sales"] > 100 else "Low")
        assert row["Scaled Sales"] == row["Sales"] * 2
        assert row["Order Year"] == 2020

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


def main() -> None:
    test_lod_fourteen_calcs_ok()
    test_lod_duckdb_matches_expected()
    test_include_exclude_region_grain()
    print("lod_test.twb: 14/14 calcs matched DuckDB expectations")


if __name__ == "__main__":
    main()
