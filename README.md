# EscapeTableau

Local-first converter: Tableau calculated fields in `.twb` / `.twbx` workbooks become ANSI SQL. Parsing and transpilation stay in memory. Files are never written unless you pass `--output-dir` or click a download.

Requires **Python 3.11+**. The web UI also needs Node.js 20+.

## Install

```bash
python3 -m pip install -r requirements.txt
```

There is no `py-tbparse` or `tableau-parser` dependency. The engine uses `xml.etree` and `zipfile` only.

## Engine

```python
from pathlib import Path
from engine import transpile

result = transpile(Path("samples/superstore_calcs.twb"))
print(result.sql_script)
print(result.markdown_audit)
```

`transpile()` accepts a path, `bytes`, or a binary file object. Packaged `.twbx` archives are unzipped in memory.

Tests:

```bash
python3 -m pytest engine/test_engine.py
```

## CLI

```bash
python cli.py process samples/superstore_calcs.twb --output-dir ./output --format all --print-summary
```

- `--format`: `sql` | `markdown` | `json` | `all` (default `all`)
- `--print-summary`: ASCII table of captions and parse status
- stdin (used by the web API): `python cli.py process - --format json < workbook.twb`
- Writes files **only** when `--output-dir` is set. JSON goes to stdout when `--format json` and no output directory.

Optional `--dialect ansi|snowflake|postgres|duckdb` tweaks `LIKE` vs `ILIKE`.

## Next.js UI

```bash
cd web
npm install
npm run dev
```

Dev server binds **0.0.0.0:4317**. Open [http://127.0.0.1:4317](http://127.0.0.1:4317). Drop a workbook or use **Try with Sample Workbook**. Results: Monaco SQL, searchable audit table, Markdown download.

`POST /api/transpile` pipes the upload to `python cli.py process - --format json` on stdin and never persists the file. Python 3.11+ and `pip install -r requirements.txt` must be available on the machine that runs Next.js.

## Streamlit (air-gapped)

```bash
streamlit run app_local.py
```

Green **Local Offline Mode** banner, file uploader, dialect sidebar (`ansi` / `snowflake` / `postgres` / `duckdb`), and tabs for SQL, dataframe, and downloads.

## Samples

- `samples/superstore_calcs.twb` — compact synthetic workbook with IIF, ZN, IFNULL, CONTAINS, LOD, and name-resolution calcs. Used by tests and the sample button.
- An optional copy of Sample Super Store from [hiranvjoseph/Tableau-Dashboard-Repository](https://github.com/hiranvjoseph/Tableau-Dashboard-Repository) was **not** vendored: that repository has no declared MIT license.

## Layout

- `engine/` — schema, parser, transpiler, audit, tests
- `cli.py` — Typer
- `app_local.py` — Streamlit
- `web/` — Next.js App Router + Tailwind + shadcn/ui
