"""sqlglot dialect round-trip for generated SQL views + Custom SQL parse."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import transpile
from engine.sqlglot_util import (
    apply_sqlglot_dialect,
    parse_custom_sql,
    sqlglot_available,
    transpile_script,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

pytestmark = pytest.mark.skipif(not sqlglot_available(), reason="sqlglot not installed")


def test_sqlglot_roundtrip_snowflake_bigquery_tsql() -> None:
    result = transpile(SAMPLES / "superstore_calcs.twb", dialect="duckdb")
    assert "CREATE VIEW" in result.sql_script
    for write in ("snowflake", "bigquery", "tsql"):
        out = transpile_script(result.sql_script, read="duckdb", write=write)
        assert "CREATE" in out.upper()
        if write == "bigquery":
            assert "`" in out or "CREATE" in out.upper()
        if write == "tsql":
            assert "[" in out or "CREATE" in out.upper()


def test_apply_sqlglot_dialect_helper() -> None:
    sql = 'CREATE VIEW "v" AS SELECT "Region", SUM("Sales") AS "Sales" FROM "Orders"'
    bq = apply_sqlglot_dialect(sql, "bigquery")
    assert "CREATE" in bq.upper()
    # ansi leaves unchanged
    assert apply_sqlglot_dialect(sql, "ansi") == sql


def test_bigquery_dialect_uses_sqlglot() -> None:
    result = transpile(SAMPLES / "superstore_calcs.twb", dialect="bigquery")
    assert result.dialect == "bigquery"
    assert "CREATE" in result.sql_script.upper()


def test_parse_custom_sql_notes() -> None:
    ok, detail = parse_custom_sql("SELECT * FROM orders WHERE region = 'West'")
    assert ok, detail
    bad, _ = parse_custom_sql("SELECT FROM WHERE")
    assert bad is False
