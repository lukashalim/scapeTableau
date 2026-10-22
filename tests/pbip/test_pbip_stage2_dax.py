"""Stage 2: measure vs column, Superstore golden DAX, LOD / table-calc fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import transpile, transpile_formula
from engine.pbip.table_calcs import transpile_table_calc_formula
from engine.schema import ParseStatus
from engine.transpiler import FormulaContext, classify_dax_kind, parse_formula

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
SUPERSTORE = Path("/cursor/stores/self/internal/US_Superstore_10.0.twbx")
if not SUPERSTORE.is_file():
    SUPERSTORE = SAMPLES / "US_Superstore_10.0.twbx"


def test_measure_vs_column_classifier() -> None:
    assert classify_dax_kind(parse_formula("SUM([Sales])")) == "measure"
    assert classify_dax_kind(parse_formula("ZN([Profit])")) == "calculated_column"
    assert classify_dax_kind(parse_formula("{FIXED [Order ID]: SUM([Profit])}")) == "measure"
    # Row-level date LOD stays calculated column
    assert (
        classify_dax_kind(parse_formula("{FIXED [Customer Name]: MIN([Order Date])}"))
        == "calculated_column"
    )
    # IF over aggregates → measure (Achieved Quota pattern)
    tree = parse_formula('IF SUM([Sales]) >= 1 THEN "Yes" ELSE "No" END')
    assert classify_dax_kind(tree) == "measure"


def test_aggregate_dax_primitives() -> None:
    cases = [
        ("SUM([Sales])", "SUM('Orders'[Sales])"),
        ("AVG([Sales])", "AVERAGE('Orders'[Sales])"),
        ("COUNTD([Customer])", "DISTINCTCOUNT('Orders'[Customer])"),
        ("ZN([Profit])", "COALESCE('Orders'[Profit], 0)"),
        ("IIF([Sales] > 0, [Profit], 0)", "IF('Orders'[Sales] > 0, 'Orders'[Profit], 0)"),
        ("ATTR([Region])", "IF(HASONEVALUE('Orders'[Region]), VALUES('Orders'[Region]), BLANK())"),
        (
            "SUM([Profit])/SUM([Sales])",
            "DIVIDE(SUM('Orders'[Profit]), SUM('Orders'[Sales]))",
        ),
    ]
    for formula, expected in cases:
        dax, status, err = transpile_formula(formula, target="dax")
        assert status == ParseStatus.ok, err
        assert dax == expected, f"{formula}: {dax!r} != {expected!r}"


def test_fixed_lod_dax_patterns() -> None:
    dax, st, err = transpile_formula("{FIXED [Region]: SUM([Sales])}", target="dax")
    assert st == ParseStatus.ok, err
    assert dax == "CALCULATE(SUM('Orders'[Sales]), ALLEXCEPT('Orders', 'Orders'[Region]))"
    none, nst, _ = transpile_formula("{SUM([Sales])}", target="dax")
    assert nst == ParseStatus.ok
    assert none == "CALCULATE(SUM('Orders'[Sales]), ALL('Orders'))"
    avg, ast, _ = transpile_formula("AVG({FIXED [Customer]: SUM([Sales])})", target="dax")
    assert ast == ParseStatus.ok
    assert avg == "AVERAGEX(VALUES('Orders'[Customer]), CALCULATE(SUM('Orders'[Sales])))"
    nested, nest_st, _ = transpile_formula(
        "{FIXED [Region]: AVG({FIXED [Customer]: SUM([Sales])})}", target="dax"
    )
    assert nest_st == ParseStatus.skipped
    assert "nested-lod" in nested


def test_include_exclude_grain() -> None:
    ctx = FormulaContext(view_dims=["Region"])
    dax, st, err = transpile_formula(
        "{INCLUDE [Category]: SUM([Sales])}", target="dax", context=ctx
    )
    assert st == ParseStatus.ok, err
    assert "ALLEXCEPT" in dax
    skipped, sst, _ = transpile_formula("{EXCLUDE [Region]: SUM([Sales])}", target="dax")
    assert sst == ParseStatus.skipped
    assert "lod-include/exclude" in skipped


def test_table_calc_dax_mappings() -> None:
    rank = transpile_table_calc_formula("Rank SUM(Sales) along Columns", dax_table="Extract")
    assert rank.status == ParseStatus.ok
    assert "RANKX" in rank.dax
    pct = transpile_table_calc_formula(
        "% of Total SUM(Number of Records) along Field", dax_table="Extract"
    )
    assert pct.status == ParseStatus.ok
    assert "DIVIDE(" in pct.dax and "ALLSELECTED" in pct.dax
    idx = transpile_table_calc_formula("Index Field along Table (Across)")
    assert idx.status == ParseStatus.skipped
    assert "index" in idx.reason.lower() or "INDEX" in idx.dax


@pytest.mark.skipif(not SUPERSTORE.is_file(), reason="US_Superstore_10.0.twbx missing")
def test_superstore_golden_measures() -> None:
    result = transpile(SUPERSTORE)
    fields = {f.caption: f for f in result.calculated_fields}

    profit_ratio = fields["Profit Ratio"]
    assert profit_ratio.dax_kind == "measure"
    assert profit_ratio.dax == (
        "DIVIDE(SUM('Sample - Superstore'[Profit]), SUM('Sample - Superstore'[Sales]))"
    )
    assert "Extract[" not in (profit_ratio.dax or "")
    assert "SUM('" not in (profit_ratio.dax or "").replace("SUM('Sample", "")

    ppc = fields["Profit per Customer"]
    assert ppc.dax_kind == "measure"
    assert ppc.dax == (
        "DIVIDE(SUM('Sample - Superstore'[Profit]), "
        "DISTINCTCOUNT('Sample - Superstore'[Customer Name]))"
    )

    achieved = fields["Achieved Quota"]
    assert achieved.dax_kind == "measure"
    assert achieved.dax_status == ParseStatus.ok
    assert "IF(" in (achieved.dax or "")
    assert "SUM(" in (achieved.dax or "")

    order_prof = fields["Order Profitable?"]
    assert order_prof.dax_kind == "calculated_column"
    assert "ALLEXCEPT" in (order_prof.dax or "")
    assert "Order ID" in (order_prof.dax or "")

    # Stage 2 table calc DAX on Rank Sales / % of Total
    if "Rank Sales" in fields:
        assert fields["Rank Sales"].dax_status == ParseStatus.ok
        assert "RANKX" in (fields["Rank Sales"].dax or "")
    pct_key = "% of Total Number of Records"
    if pct_key in fields:
        assert fields[pct_key].dax_status == ParseStatus.ok
        assert "ALLSELECTED" in (fields[pct_key].dax or "")
