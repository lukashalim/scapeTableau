"""Default numeric check harness: run generated SQL views on CSV/XLSX via DuckDB.

Used by pytest only. Do not import from Vercel serverless handlers — DuckDB
native libs blow the serverless size budget. Hyper extracts are optional and
only when tableauhyperapi is available locally.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


def connect_with_csv(csv_path: Path, table: str = "Orders") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?, header=true)",
        [str(csv_path)],
    )
    return con


def connect_with_xlsx(xlsx_path: Path, sheet: str | None = None, table: str = "Orders") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    # DuckDB spatial/excel extension may be unavailable — fall back via pandas.
    try:
        import pandas as pd

        df = pd.read_excel(xlsx_path, sheet_name=sheet or 0)
        con.register(table, df)
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM {table}")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"DuckDB XLSX load failed: {exc}") from exc
    return con


def exec_view_script(con: duckdb.DuckDBPyConnection, sql_script: str) -> None:
    """Execute CREATE VIEW statements from a transpile sql_script (best-effort)."""

    for chunk in sql_script.split(";"):
        stmt = chunk.strip()
        if not stmt or stmt.startswith("--"):
            # Keep statements that start with comments but contain CREATE
            if "CREATE" not in stmt.upper():
                continue
        if "CREATE" not in stmt.upper():
            continue
        try:
            con.execute(stmt)
        except Exception:
            # Individual views may reference missing columns — harness callers assert selectively
            pass


def query_scalar(con: duckdb.DuckDBPyConnection, sql: str):
    return con.execute(sql).fetchone()[0]
