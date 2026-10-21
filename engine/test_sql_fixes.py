"""Regression tests for SQL generator fixes (US Superstore 10.0 workbook)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import transpile, transpile_formula
from engine.schema import ParseStatus
from engine.transpiler import (
    normalize_number_literal,
    quote_table_ref,
    split_tableau_ident,
)

ROOT = Path(__file__).resolve().parents[1]
SUPERSTORE = ROOT / "samples" / "US_Superstore_10.0.twbx"
SAMPLE = ROOT / "samples" / "superstore_calcs.twb"


@pytest.fixture(scope="module")
def superstore_ansi():
    assert SUPERSTORE.is_file(), f"missing {SUPERSTORE}"
    return transpile(SUPERSTORE, dialect="ansi")


def test_quote_table_ref_splits_extract_brackets() -> None:
    assert split_tableau_ident("[Extract].[Extract]") == ["Extract", "Extract"]
    assert quote_table_ref("[Extract].[Extract]") == '"Extract"."Extract"'
    assert quote_table_ref("Extract].[Extract") == '"Extract"."Extract"'
    assert quote_table_ref("[Orders]") == '"Orders"'
    assert quote_table_ref("[Extract].[Extract]", dialect="tsql") == "[Extract].[Extract]"


def test_normalize_float_noise() -> None:
    assert normalize_number_literal("18.400000000000002") == "18.4"
    assert normalize_number_literal("0.59999999999999998") == "0.6"
    assert normalize_number_literal("0.064000000000000001") == "0.064"
    assert normalize_number_literal("50000") == "50000"


def test_tableau_null_is_bare_null() -> None:
    sql, status, err = transpile_formula(
        "if year([Order Date])=2013 then [Sales] else NULL end"
    )
    assert status == ParseStatus.ok
    assert err is None
    assert "ELSE NULL" in sql
    assert '"NULL"' not in sql


def test_superstore_extract_quoting_and_distinct_views(superstore_ansi) -> None:
    script = superstore_ansi.sql_script
    assert '"Extract].[Extract"' not in script
    assert '[Extract].[Extract] → "Extract"."Extract"' in script
    assert 'CREATE VIEW "Sales Commission" AS' in script
    assert 'CREATE VIEW "Sample - Superstore" AS' in script
    # Distinct FROM targets (caption), not colliding broken Extract quotes
    assert 'FROM "Sales Commission";' in script
    assert 'FROM "Sample - Superstore";' in script
    assert script.count("CREATE VIEW") >= 2


def test_superstore_null_and_float_literals(superstore_ansi) -> None:
    sales_2013 = next(f for f in superstore_ansi.calculated_fields if f.caption == "2013 sales")
    assert sales_2013.sql.endswith("ELSE NULL END") or " ELSE NULL END" in sales_2013.sql
    assert '"NULL"' not in sales_2013.sql

    rate = next(f for f in superstore_ansi.calculated_fields if f.caption == "Commission Rate")
    assert rate.sql == "18.4"
    growth = next(f for f in superstore_ansi.calculated_fields if f.caption == "New Business Growth")
    assert growth.sql == "0.6"
    assert "18.400000000000002" not in superstore_ansi.sql_script
    assert "0.59999999999999998" not in superstore_ansi.sql_script


def test_superstore_preserves_original_names(superstore_ansi) -> None:
    script = superstore_ansi.sql_script
    assert 'AS "Achievement (estimated)"' in script
    assert "Achievement _estimated_" not in script
    assert 'CREATE VIEW "Sample - Superstore" AS' in script
    assert "Sample _ Superstore" not in script


def test_superstore_ansi_avoids_vendor_datediff(superstore_ansi) -> None:
    script = superstore_ansi.sql_script
    assert "DATEDIFF(" not in script
    days = next(f for f in superstore_ansi.calculated_fields if f.caption == "Days to Ship Actual")
    assert "CAST(" in days.sql and "AS DATE)" in days.sql


def test_dialect_date_and_bool_variants() -> None:
    duck = transpile(SUPERSTORE, dialect="duckdb")
    assert "DATE_DIFF('day'" in duck.sql_script

    snow = transpile(SUPERSTORE, dialect="snowflake")
    assert "DATEDIFF('day'" in snow.sql_script

    tsql = transpile(SUPERSTORE, dialect="tsql")
    compact = tsql.sql_script.replace(" ", "")
    assert "DATEDIFF(day," in compact
    profitable = next(f for f in tsql.calculated_fields if "Profitable" in f.caption)
    assert profitable.sql.startswith("CASE WHEN") and profitable.sql.endswith("THEN 1 ELSE 0 END")


def test_parameter_comments_and_constant_case_flag(superstore_ansi) -> None:
    script = superstore_ansi.sql_script
    assert "-- Parameters (inlined at current workbook values; Power BI what-if later)" in script
    assert "Commission Rate: current=18.4" in script
    assert "Sort by: current=Names" in script
    assert "allowed=" in script
    assert "NOTE: CASE is constant after parameter inline (Sort by field)" in script
    sort_field = next(f for f in superstore_ansi.calculated_fields if f.caption == "Sort by field")
    assert "CASE WHEN 'Names' = 'Names' THEN 0" in sort_field.sql


def test_one_view_per_datasource_still_holds() -> None:
    result = transpile(SAMPLE, dialect="ansi")
    assert result.sql_script.count("CREATE VIEW") == 2
    assert 'CREATE VIEW "Sample Superstore" AS' in result.sql_script
    assert 'CREATE VIEW "Profit Ratio" AS' not in result.sql_script
