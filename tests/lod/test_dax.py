"""Exact DAX for every calculated field in lod_test.twb."""

from __future__ import annotations

from pathlib import Path

from engine import transpile
from engine.schema import ParseStatus

HERE = Path(__file__).resolve().parent
TWB = HERE / "lod_test.twb"

EXPECTED_DAX = {
    "Fixed Region Sales": "CALCULATE(SUM(Orders[Sales]), ALLEXCEPT(Orders, Orders[Region]))",
    "Grand Total Sales": "CALCULATE(SUM(Orders[Sales]), REMOVEFILTERS(Orders))",
    "Include Category Sales": (
        "CALCULATE(SUM(Orders[Sales]), ALLEXCEPT(Orders, Orders[Region], Orders[Category]))"
    ),
    "Avg Customer Sales": (
        "AVERAGEX(SUMMARIZE(Orders, Orders[Region], Orders[Customer]), CALCULATE(SUM(Orders[Sales])))"
    ),
    "Exclude Region Sales": "CALCULATE(SUM(Orders[Sales]), REMOVEFILTERS(Orders[Region]))",
    "Region Customers": "CALCULATE(DISTINCTCOUNT(Orders[Customer]), ALLEXCEPT(Orders, Orders[Region]))",
    "Nested Min Category": (
        "CALCULATE(MINX(SUMMARIZE(Orders, Orders[Region], Orders[Category]), "
        "CALCULATE(SUM(Orders[Sales]), ALLEXCEPT(Orders, Orders[Region], Orders[Category]))), "
        "ALLEXCEPT(Orders, Orders[Region]))"
    ),
    "Region Share": "DIVIDE([Fixed Region Sales], [Grand Total Sales])",
    "Region Case": 'SWITCH(Orders[Region], "East", 1, "West", 2, 0)',
    "Sales Band": 'SWITCH(TRUE(), Orders[Sales] > 100, "High", "Low")',
    "Scaled Sales": "Orders[Sales] * [Multiplier Value]",
    "Order Year": "YEAR(Orders[Order Date])",
    "Ship Days": "DATEDIFF(Orders[Order Date], Orders[Ship Date], DAY)",
    "Order Month": "DATE(YEAR(Orders[Order Date]), MONTH(Orders[Order Date]), 1)",
    "Next Month": "EDATE(Orders[Order Date], 1)",
}

EXPECTED_KIND = {
    "Fixed Region Sales": "measure",
    "Grand Total Sales": "measure",
    "Include Category Sales": "measure",
    "Avg Customer Sales": "measure",
    "Exclude Region Sales": "measure",
    "Region Customers": "measure",
    "Nested Min Category": "measure",
    "Region Share": "measure",
    "Region Case": "calculated_column",
    "Sales Band": "calculated_column",
    "Scaled Sales": "calculated_column",
    "Order Year": "calculated_column",
    "Ship Days": "calculated_column",
    "Order Month": "calculated_column",
    "Next Month": "calculated_column",
}


def test_all_lod_calcs_dax_exact() -> None:
    result = transpile(TWB, dialect="duckdb")
    fields = {f.caption: f for f in result.calculated_fields}
    assert set(EXPECTED_DAX) <= set(fields)
    for caption, expected in EXPECTED_DAX.items():
        field = fields[caption]
        assert field.dax_status == ParseStatus.ok, f"{caption}: {field.dax_status} {field.error} {field.dax}"
        assert field.dax == expected, f"{caption}\n{field.dax!r}\n!=\n{expected!r}"
        assert field.dax_kind == EXPECTED_KIND[caption]
        assert "// TODO" not in field.dax
    blob = "\n".join(f.dax for f in result.calculated_fields)
    assert "// TODO" not in blob
    assert "// TODO" not in result.dax_script
    assert "dax_status=" in result.dax_script
    assert "// Measures" in result.dax_script
    assert "// Calculated columns" in result.dax_script
    assert "[Fixed Region Sales] :=" in result.dax_script
    assert "Orders[Next Month] =" in result.dax_script
    assert "[Multiplier Value] := 2" in result.dax_script
    assert "[Multiplier Value] = 2" not in result.dax_script
    assert "/* [Parameters].[Multiplier] */" not in result.dax_script
    assert "/* [Parameters]" not in (fields["Scaled Sales"].dax or "")
    assert "DATEADD(Orders[Order Date], 1, MONTH)" not in (fields["Next Month"].dax or "")


def test_avg_include_sql_groups_customers() -> None:
    result = transpile(TWB, dialect="duckdb")
    field = next(f for f in result.calculated_fields if f.caption == "Avg Customer Sales")
    sql = field.sql
    assert "GROUP BY" in sql
    assert '"Customer"' in sql
    assert "AVG(lod_inner)" in sql
    assert "OVER (" not in sql
