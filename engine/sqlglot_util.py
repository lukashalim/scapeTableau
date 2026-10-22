"""sqlglot helpers: dialect rewrite of generated SQL + Custom SQL parse notes.

Pure Python — safe for CLI, desktop sidecar, and Vercel. Browser sqlglot.js is
optional later and not required.
"""

from __future__ import annotations

from dataclasses import dataclass

# Map BIEscape dialect names → sqlglot dialect ids
_SQLGLOT_WRITE = {
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "tsql": "tsql",
    "postgres": "postgres",
    "duckdb": "duckdb",
    "ansi": "duckdb",  # closest portable read/write for ANSI-ish scripts
}

SQLGLOT_DIALECTS = frozenset(_SQLGLOT_WRITE)


@dataclass
class CustomSqlParseNote:
    name: str | None
    datasource: str | None
    ok: bool
    detail: str


def sqlglot_available() -> bool:
    try:
        import sqlglot  # noqa: F401
    except Exception:
        return False
    return True


def transpile_script(sql: str, *, read: str = "duckdb", write: str = "snowflake") -> str:
    """Rewrite a multi-statement SQL script with sqlglot.

    Falls back to the original script if sqlglot is missing or a statement fails.
    """

    if not sql or not sql.strip():
        return sql
    if not sqlglot_available():
        return sql
    import sqlglot

    read_d = _SQLGLOT_WRITE.get(read.lower(), read.lower())
    write_d = _SQLGLOT_WRITE.get(write.lower(), write.lower())
    if read_d == write_d and write.lower() not in {"snowflake", "bigquery", "tsql"}:
        return sql

    chunks: list[str] = []
    # Preserve leading comment banners by splitting on CREATE VIEW / CREATE OR REPLACE
    statements = _split_sql_statements(sql)
    for stmt in statements:
        stripped = stmt.strip()
        if not stripped:
            continue
        if stripped.startswith("--") and "CREATE" not in stripped.upper():
            chunks.append(stripped)
            continue
        try:
            out = sqlglot.transpile(stripped, read=read_d, write=write_d, pretty=True)
            chunks.append(out[0] if out else stripped)
        except Exception as exc:  # noqa: BLE001 — keep original statement
            chunks.append(f"-- sqlglot skipped ({exc}):\n{stripped}")
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def _split_sql_statements(sql: str) -> list[str]:
    """Naive split on semicolons outside comments — good enough for our view scripts."""

    parts: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines(keepends=True):
        buf.append(line)
        if ";" in line and not line.lstrip().startswith("--"):
            parts.append("".join(buf).rstrip().rstrip(";"))
            buf = []
    if buf:
        parts.append("".join(buf).rstrip().rstrip(";"))
    return parts


def parse_custom_sql(sql: str, *, read: str = "postgres") -> tuple[bool, str]:
    """Return (ok, message) after attempting to parse Custom SQL with sqlglot."""

    if not sql or not sql.strip():
        return False, "empty custom SQL"
    if not sqlglot_available():
        return False, "sqlglot not installed"
    import sqlglot

    read_d = _SQLGLOT_WRITE.get(read.lower(), read.lower())
    try:
        sqlglot.parse_one(sql, read=read_d)
        return True, f"parsed as {read_d}"
    except Exception as exc:  # noqa: BLE001
        # Retry with generic dialect
        try:
            sqlglot.parse_one(sql, read=None)
            return True, f"parsed (generic) after {read_d} failed: {exc}"
        except Exception as exc2:  # noqa: BLE001
            return False, str(exc2)


def custom_sql_notes(workbook_datasources) -> list[CustomSqlParseNote]:
    """Inspect Tableau relation custom SQL via sqlglot (non-fatal)."""

    notes: list[CustomSqlParseNote] = []
    for ds in workbook_datasources or []:
        for rel in getattr(ds, "relations", None) or []:
            custom = getattr(rel, "custom_sql", None)
            if not custom:
                continue
            ok, detail = parse_custom_sql(custom)
            notes.append(
                CustomSqlParseNote(
                    name=getattr(rel, "name", None) or getattr(rel, "caption", None),
                    datasource=getattr(ds, "caption", None) or getattr(ds, "name", None),
                    ok=ok,
                    detail=detail,
                )
            )
    return notes


def apply_sqlglot_dialect(sql_script: str, dialect: str) -> str:
    """If dialect is a warehouse target, rewrite the script from duckdb → target."""

    d = (dialect or "ansi").lower()
    if d in {"ansi", "duckdb", "postgres", "dax"}:
        return sql_script
    if d not in _SQLGLOT_WRITE:
        return sql_script
    return transpile_script(sql_script, read="duckdb", write=d)
