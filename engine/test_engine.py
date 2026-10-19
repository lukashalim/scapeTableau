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
    assert any(ds.name.lower() == "parameters" for ds in result.datasources)

    dumped = json.loads(result.model_dump_json())
    blob = json.dumps(dumped)
    assert "s3cret" not in blob
    assert "hunter2" not in blob
    west = next(ds for ds in result.datasources if ds.caption == "West Custom SQL")
    assert any(conn.password == "***" for conn in west.connections)
    assert any(conn.server and "PWD=***" in conn.server for conn in west.connections)
    assert any(rel.type == "text" and rel.custom_sql and "West" in rel.custom_sql for rel in west.relations)

    hidden = [f for f in (result.inspect.fields if result.inspect else []) if f.hidden]
    assert any(f.caption == "Internal Order Id" for f in hidden)
    assert result.inspect and any(p.caption == "Profit Goal" for p in result.inspect.parameters)
    assert any(d.name == "Overview Dash" for d in result.dashboards)
    assert dumped.get("inspect")
    assert dumped.get("extract_inventory")

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


def test_hidden_parameter_dashboard_on_sample() -> None:
    from engine.parser import parse_workbook

    parsed = parse_workbook(SAMPLE)
    assert any(f.hidden and f.caption == "Internal Order Id" for f in parsed.fields)
    assert any(p.caption == "Profit Goal" for p in parsed.parameters)
    assert parsed.dashboards and parsed.dashboards[0].zones


def test_csv_to_xlsx_and_hyper_listed_not_converted() -> None:
    from openpyxl import load_workbook

    from engine.extract import extract_to_xlsx

    twbx = ROOT / "samples" / "superstore_calcs.twbx"
    xlsx, inventory = extract_to_xlsx(twbx)
    assert any(m.kind == "csv" and m.path.endswith("Orders.csv") for m in inventory.archive_members)
    book = load_workbook(io.BytesIO(xlsx), read_only=True)
    names = book.sheetnames
    book.close()
    assert any("Orders" in name for name in names)
    assert any(name == "Custom SQL" for name in names)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("packaged.twb", SAMPLE.read_bytes())
        zf.writestr("extracts/sales.hyper", b"HYPERFAKE")
    xlsx2, inv2 = extract_to_xlsx(buf.getvalue())
    assert any(m.kind == "hyper" and m.path.endswith("sales.hyper") for m in inv2.hyper_files)
    book2 = load_workbook(io.BytesIO(xlsx2), read_only=True)
    try:
        assert "Hyper files" in book2.sheetnames
        rows = list(book2["Hyper files"].iter_rows(values_only=True))
        joined = " ".join(str(c) for row in rows for c in row)
        assert "sales.hyper" in joined
        assert "HYPERFAKE" not in joined
    finally:
        book2.close()
    assert all("sales.hyper" != sheet for sheet in inv2.converted_sheets)


def test_password_redaction_connect_string() -> None:
    from engine.redact import redact_connect_fragment, redact_password_attr

    assert redact_password_attr("s3cret") == "***"
    assert redact_connect_fragment("host;PWD=hunter2;Database=x") == "host;PWD=***;Database=x"
    assert redact_connect_fragment("Password=abc;foo=1") == "Password=***;foo=1"


def test_asgi_app_post_extract() -> None:
    import asyncio

    from app import app as asgi_app

    payload = (ROOT / "samples" / "superstore_calcs.twbx").read_bytes()
    boundary = "----scapeBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="superstore_calcs.twbx"\r\n'
        "Content-Type: application/zip\r\n\r\n"
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
        "path": "/api/extract",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
    }
    asyncio.run(asgi_app(scope, receive, send))
    assert sent[0]["status"] == 200
    headers = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    assert "spreadsheetml.sheet" in headers["content-type"]
    assert sent[1]["body"][:2] == b"PK"


def test_cli_inspect_and_extract(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from cli import app

    runner = CliRunner()
    inspected = runner.invoke(app, ["inspect", str(SAMPLE), "--format", "json"])
    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.stdout)
    assert payload["counts"]["parameters"] >= 1
    assert "s3cret" not in inspected.stdout

    out = tmp_path / "extract.xlsx"
    extracted = runner.invoke(app, ["extract", str(ROOT / "samples" / "superstore_calcs.twbx"), "--output", str(out)])
    assert extracted.exit_code == 0, extracted.output
    assert out.exists() and out.read_bytes()[:2] == b"PK"


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
