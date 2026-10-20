#!/usr/bin/env python3
"""EscapeTableau CLI — transpile Tableau workbooks to ANSI SQL."""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

import typer
from tabulate import tabulate

from engine import extract_to_xlsx, transpile
from engine.inspect import inspect_markdown
from engine.schema import WorkbookParseError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Convert Tableau calculated fields in .twb / .twbx files to ANSI SQL and DAX.",
)


@app.callback()
def _root() -> None:
    """EscapeTableau CLI."""
    return None


class OutputFormat(str, Enum):
    sql = "sql"
    markdown = "markdown"
    json = "json"
    all = "all"
    dax = "dax"
    dbt = "dbt"


class InspectFormat(str, Enum):
    json = "json"
    markdown = "markdown"


def _read_source(path: str) -> bytes | Path:
    if path == "-":
        return sys.stdin.buffer.read()
    return Path(path)


def _write_outputs(
    output_dir: Path,
    fmt: OutputFormat,
    sql: str,
    md: str,
    json_text: str,
    dax: str = "",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if fmt in {OutputFormat.sql, OutputFormat.all, OutputFormat.dbt}:
        (output_dir / "escapetableau.sql").write_text(sql, encoding="utf-8")
    if fmt in {OutputFormat.dax, OutputFormat.all}:
        (output_dir / "escapetableau.dax").write_text(dax, encoding="utf-8")
    if fmt in {OutputFormat.markdown, OutputFormat.all}:
        (output_dir / "audit.md").write_text(md, encoding="utf-8")
    if fmt in {OutputFormat.json, OutputFormat.all}:
        (output_dir / "result.json").write_text(json_text, encoding="utf-8")


def _print_body(fmt: OutputFormat, sql: str, md: str, json_text: str, dax: str = "") -> None:
    if fmt == OutputFormat.json:
        sys.stdout.write(json_text)
        if not json_text.endswith("\n"):
            sys.stdout.write("\n")
        return
    if fmt in {OutputFormat.sql, OutputFormat.dbt}:
        sys.stdout.write(sql)
        return
    if fmt == OutputFormat.dax:
        sys.stdout.write(dax)
        return
    if fmt == OutputFormat.markdown:
        sys.stdout.write(md)
        return
    sys.stdout.write("-- SQL\n")
    sys.stdout.write(sql)
    sys.stdout.write("\n-- DAX\n")
    sys.stdout.write(dax)
    sys.stdout.write("\n-- AUDIT\n")
    sys.stdout.write(md)
    sys.stdout.write("\n-- JSON\n")
    sys.stdout.write(json_text)
    sys.stdout.write("\n")


@app.command("process")
def process(
    path: str = typer.Argument(
        ...,
        metavar="PATH",
        help="Path to a .twb/.twbx workbook, or - to read bytes from stdin.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="If set, write artifacts here. Nothing is written unless this option is provided.",
    ),
    format: OutputFormat = typer.Option(
        OutputFormat.all,
        "--format",
        help="sql | markdown | json | dax | dbt | all",
        case_sensitive=False,
    ),
    print_summary: bool = typer.Option(
        False,
        "--print-summary",
        help="Print an ASCII summary table (stderr when --format json so stdout stays JSON).",
    ),
    dialect: str = typer.Option(
        "ansi",
        "--dialect",
        help="ansi | snowflake | postgres | duckdb | dax",
    ),
    target: str = typer.Option(
        "sql",
        "--target",
        help="sql | dax (status/script emphasis; JSON always includes both)",
    ),
) -> None:
    try:
        source = _read_source(path)
        script_style = "dbt" if format == OutputFormat.dbt else "sql"
        result = transpile(source, dialect=dialect, target=target, script_style=script_style)
    except WorkbookParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # pragma: no cover
        typer.secho(f"Conversion failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    json_text = result.model_dump_json(indent=2)
    if print_summary:
        rows = [
            [f.caption, f.datasource, f.status.value, (f.error or "")[:40]]
            for f in result.calculated_fields
        ]
        table = tabulate(
            rows,
            headers=["Caption", "Datasource", "Status", "Notes"],
            tablefmt="simple",
        )
        stream = sys.stderr if format == OutputFormat.json and output_dir is None else sys.stdout
        stream.write(table + "\n")
        if format == OutputFormat.json and output_dir is None:
            stream.write("\n")

    if output_dir is not None:
        _write_outputs(
            output_dir,
            format,
            result.sql_script,
            result.markdown_audit,
            json_text,
            dax=result.dax_script,
        )
        typer.secho(f"Wrote {format.value} artifacts to {output_dir}", fg=typer.colors.GREEN, err=True)
        return

    _print_body(format, result.sql_script, result.markdown_audit, json_text, dax=result.dax_script)


@app.command("inspect")
def inspect_cmd(
    path: str = typer.Argument(..., metavar="PATH", help="Workbook path, or - for stdin."),
    format: InspectFormat = typer.Option(
        InspectFormat.json,
        "--format",
        help="json | markdown",
        case_sensitive=False,
    ),
) -> None:
    try:
        result = transpile(_read_source(path))
    except WorkbookParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    snapshot = result.inspect
    if snapshot is None:
        typer.secho("Inspect payload missing", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if format == InspectFormat.markdown:
        sys.stdout.write(inspect_markdown(snapshot))
        return
    sys.stdout.write(snapshot.model_dump_json(indent=2))
    sys.stdout.write("\n")


@app.command("extract")
def extract_cmd(
    path: str = typer.Argument(..., metavar="PATH", help="Workbook path, or - for stdin (xlsx on stdout)."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write .xlsx here. Omit with PATH=- to stream xlsx bytes to stdout.",
    ),
) -> None:
    try:
        xlsx, inventory = extract_to_xlsx(_read_source(path))
    except WorkbookParseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # pragma: no cover
        typer.secho(f"Extract failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if inventory.hyper_files:
        for note in inventory.notes:
            if "Hyper" in note or "hyper" in note:
                typer.secho(note, fg=typer.colors.YELLOW, err=True)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(xlsx)
        typer.secho(f"Wrote {output}", fg=typer.colors.GREEN, err=True)
        return
    sys.stdout.buffer.write(xlsx)


if __name__ == "__main__":
    app()
