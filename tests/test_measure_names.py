"""Measure Names / Measure Values handling."""

from __future__ import annotations

from pathlib import Path

from engine import transpile
from engine.measure_names import is_measure_names_token, is_measure_values_token
from engine.parser import parse_workbook

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "samples" / "measure_names.twb"
SUPERSTORE = ROOT / "tests" / "samples" / "US_Superstore_10.0.twbx"
# Fallback when tests/samples copy is missing
SUPERSTORE_ALT = ROOT / "samples" / "US_Superstore_10.0.twbx"


def _superstore() -> Path:
    if SUPERSTORE.is_file():
        return SUPERSTORE
    return SUPERSTORE_ALT


def test_mn_mv_token_helpers() -> None:
    assert is_measure_names_token("[:Measure Names]")
    assert is_measure_names_token(":Measure Names")
    assert is_measure_values_token("[Multiple Values]")
    assert is_measure_values_token("Measure Values")
    assert not is_measure_names_token("Sales")


def test_sample_strategies_a_b_c() -> None:
    result = transpile(SAMPLE, dialect="ansi")
    by_sheet = {u.worksheet: u for u in result.measure_names}
    assert set(by_sheet) >= {"ValuesWell", "NamesWell", "NamesValuesMatrix"}

    a = by_sheet["ValuesWell"]
    assert a.strategy == "a"
    assert a.measures == ["Sales", "Profit"]

    b = by_sheet["NamesWell"]
    assert b.strategy == "b"
    assert b.measures == ["Quantity"]

    c = by_sheet["NamesValuesMatrix"]
    assert c.strategy == "c"
    assert "Sales" in c.measures
    assert "Profit Ratio" in c.measures

    # Never emit MN/MV as SQL columns
    assert "[:Measure Names]" not in result.sql_script
    assert 'AS "Measure Names"' not in result.sql_script
    assert 'AS "Multiple Values"' not in result.sql_script
    assert "Worksheet 'ValuesWell' uses Measure Names/Values:" in result.sql_script

    # DAX strategy comments + raw SUM stubs
    assert "strategy a — Values well" in result.dax_script
    assert "strategy b — Names well" in result.dax_script
    assert "strategy c — Names × Values matrix" in result.dax_script
    assert "[Sales] := SUM(" in result.dax_script

    assert "Measure Names / Values conversion" in result.conversion_summary
    assert "ValuesWell" in result.conversion_summary


def test_worksheets_scrub_mn_mv_from_field_lists() -> None:
    wb = parse_workbook(SAMPLE)
    for sheet in wb.worksheets:
        assert not any("Measure Names" in f for f in sheet.used_fields)
        assert not any("Multiple Values" in f for f in sheet.cols + sheet.rows)


def test_superstore_product_detail_sheet() -> None:
    path = _superstore()
    assert path.is_file(), f"missing {path}"
    result = transpile(path, dialect="ansi")
    pd = next(u for u in result.measure_names if u.worksheet == "ProductDetailSheet")
    assert pd.strategy == "a"
    assert pd.measures == [
        "Sales",
        "Quantity",
        "Discount",
        "Profit",
        "Profit Ratio",
        "Days to Ship Scheduled",
        "Days to Ship Actual",
    ]
    expected = (
        "-- Worksheet 'ProductDetailSheet' uses Measure Names/Values: "
        "[Sales], [Quantity], [Discount], [Profit], [Profit Ratio], "
        "[Days to Ship Scheduled], [Days to Ship Actual] (on Columns / Text)"
    )
    assert expected in result.sql_script
    assert "[:Measure Names]" not in result.sql_script
    assert 'AS "Measure Names"' not in result.sql_script
    assert "strategy a — Values well" in result.dax_script
    assert any(line.startswith("[Sales] := SUM(") for line in result.dax_script.splitlines())
    assert "ProductDetailSheet" in result.conversion_summary
    # Calculated fields must not include MN/MV tokens
    captions = {f.caption for f in result.calculated_fields}
    assert "Measure Names" not in captions
    assert "Measure Values" not in captions
    assert "Multiple Values" not in captions
