from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from engine import transpile, transpile_formula
from engine.schema import ParseStatus, WorkbookParseError

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "superstore_calcs.twb"


def test_iif_to_case_when() -> None:
    sql, status, err = transpile_formula('IIF([Sales] > 0, [Profit], 0)')
    assert status == ParseStatus.ok
    assert err is None
    assert sql == 'CASE WHEN "Sales" > 0 THEN "Profit" ELSE 0 END'


def test_zn_to_coalesce() -> None:
    sql, status, err = transpile_formula("ZN([Profit])")
    assert status == ParseStatus.ok
    assert err is None
    assert sql == 'COALESCE("Profit", 0)'


def test_ifnull_to_coalesce() -> None:
    sql, status, _ = transpile_formula("IFNULL([Discount], 0)")
    assert status == ParseStatus.ok
    assert sql == 'COALESCE("Discount", 0)'


def test_contains_literal_like() -> None:
    sql, status, _ = transpile_formula('CONTAINS([Product Name], "Table")')
    assert status == ParseStatus.ok
    assert sql == "\"Product Name\" LIKE '%Table%'"


def test_contains_expression_concat() -> None:
    sql, status, _ = transpile_formula("CONTAINS([Product Name], [Category])")
    assert status == ParseStatus.ok
    assert sql == "\"Product Name\" LIKE '%' || \"Category\" || '%'"


def test_lod_unparseable_comment() -> None:
    formula = "{FIXED [Region] : SUM([Sales])}"
    sql, status, err = transpile_formula(formula)
    assert status == ParseStatus.unparseable
    assert err == "LOD expression"
    assert sql.startswith("-- TODO: Review LOD logic:")
    assert "FIXED" in sql


def test_if_elseif_end() -> None:
    sql, status, _ = transpile_formula(
        'IF [Sales] > 1000 THEN "High" ELSEIF [Sales] > 500 THEN "Medium" ELSE "Low" END'
    )
    assert status == ParseStatus.ok
    assert sql == (
        'CASE WHEN "Sales" > 1000 THEN \'High\' '
        'WHEN "Sales" > 500 THEN \'Medium\' ELSE \'Low\' END'
    )


def test_unknown_function_does_not_crash() -> None:
    sql, status, err = transpile_formula("SOMEUNKNOWN([Sales])")
    assert status == ParseStatus.unparseable
    assert "SOMEUNKNOWN" in (err or "")
    assert "TODO: unmapped Tableau function" in sql


def test_sample_workbook_round_trip() -> None:
    result = transpile(SAMPLE)
    captions = {field.caption for field in result.calculated_fields}
    assert "Profit Ratio" in captions
    assert "Profit or Zero" in captions
    assert "Has Table" in captions
    assert "Regional Sales" in captions
    assert "West Profit" in captions

    zn = next(f for f in result.calculated_fields if f.caption == "Profit or Zero")
    assert zn.sql == 'COALESCE("Profit", 0)'
    assert zn.raw_formula == zn.formula
    assert zn.sql_expression == zn.sql
    assert zn.status == ParseStatus.ok
    dumped = json.loads(result.model_dump_json())
    first = dumped["calculated_fields"][0]
    assert "formula" in first and "sql" in first
    assert "raw_formula" not in first
    assert "sql_expression" not in first

    contains = next(f for f in result.calculated_fields if f.caption == "Has Table")
    assert contains.sql == "\"Product Name\" LIKE '%Table%'"

    lod = next(f for f in result.calculated_fields if f.caption == "Regional Sales")
    assert lod.status == ParseStatus.unparseable

    resolved = next(f for f in result.calculated_fields if f.caption == "Ratio Abs")
    assert '"Profit Ratio"' in resolved.sql
    assert "Calculation_" not in resolved.sql

    ds_names = {ds.caption or ds.name for ds in result.datasources}
    assert "Sample Superstore" in ds_names
    assert "West Custom SQL" in ds_names
    assert all(ds.name.lower() != "parameters" for ds in result.datasources)

    west = next(ds for ds in result.datasources if ds.caption == "West Custom SQL")
    assert any(rel.type == "text" and rel.custom_sql and "West" in rel.custom_sql for rel in west.relations)

    assert "CREATE VIEW" in result.sql_script
    assert "Profit Ratio" in result.markdown_audit
    assert "| Caption |" in result.markdown_audit


def test_twbx_bytes_round_trip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("superstore_calcs.twb", SAMPLE.read_bytes())
    result = transpile(buf.getvalue())
    assert any(f.caption == "ZN" or f.caption == "Profit or Zero" for f in result.calculated_fields)


def test_invalid_xml_raises() -> None:
    with pytest.raises(WorkbookParseError, match="Invalid Tableau XML"):
        transpile(b"<not-tableau")


def test_invalid_zip_raises() -> None:
    with pytest.raises(WorkbookParseError, match="Invalid"):
        transpile(b"PK\x03\x04not-a-real-zip")


def test_asgi_app_post_transpile() -> None:
    import asyncio

    from app import app as asgi_app

    payload = SAMPLE.read_bytes()
    boundary = "----scapeBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="superstore_calcs.twb"\r\n'
        "Content-Type: application/xml\r\n\r\n"
    ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")

    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict:
        return messages.pop(0)

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/transpile",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
    }
    asyncio.run(asgi_app(scope, receive, send))
    assert sent[0]["status"] == 200
    data = json.loads(sent[1]["body"].decode("utf-8"))
    assert len(data["calculated_fields"]) >= 1
    assert "formula" in data["calculated_fields"][0]


def test_vercel_fn_json_keys_match_api() -> None:
    from engine.vercel_fn import transpile_payload

    status, payload = transpile_payload("superstore_calcs.twb", SAMPLE.read_bytes())
    assert status == 200
    body = json.loads(payload.decode("utf-8"))
    field = body["calculated_fields"][0]
    assert "formula" in field
    assert "sql" in field
    assert "raw_formula" not in field
    assert "sql_expression" not in field


def test_cli_json_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from cli import app

    runner = CliRunner()
    payload = SAMPLE.read_bytes()
    result = runner.invoke(app, ["process", "-", "--format", "json"], input=payload)
    assert result.exit_code == 0, result.output
    assert '"calculated_fields"' in result.stdout

    out_dir = tmp_path / "out"
    written = runner.invoke(
        app,
        ["process", str(SAMPLE), "--output-dir", str(out_dir), "--format", "all", "--print-summary"],
    )
    assert written.exit_code == 0, written.output
    assert (out_dir / "escapetableau.sql").exists()
    assert (out_dir / "audit.md").exists()
    assert (out_dir / "result.json").exists()
    assert "Profit Ratio" in written.output
