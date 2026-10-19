# BIEscape

Instantly Extract & Transpile Tableau Calculated Fields into Clean ANSI SQL & dbt Models.

BIEscape (source: lukashalim/scapeTableau) is a zero-dependency, pure-Python engine designed to automate the painful process of migrating legacy Tableau workbooks (.twb and .twbx) into modern cloud data stacks (Snowflake, BigQuery, Postgres, DuckDB, and dbt). Live site: https://www.biescape.com.

Parsing and transpilation execute 100% in-memory. Your files are never persisted to disk, uploaded to cloud storage, or sent to external LLM APIs—making it fully compliant with strict corporate data privacy policies.

## Key Features

- **In-Memory & Air-Gapped:** Zero disk writes and zero telemetry. Ideal for sensitive corporate workbook audits.
- **Zero-Dependency Python Engine:** Built entirely on standard Python utilities (`xml.etree` and `zipfile`). Fast, lightweight, and deterministic.
- **Smart AST Formula Transpilation:** Recursively transpiles Tableau-specific syntax (`IIF`, `ZN`, `IFNULL`, `STARTSWITH`, `CONTAINS`, `CASE`) into standard ANSI SQL.
- **Graceful LOD Fallbacks:** Complex Level of Detail (`FIXED`, `INCLUDE`, `EXCLUDE`) calculations are cleanly isolated and commented with `-- TODO: Review LOD logic` rather than crashing.
- **Extract Data:** Packaged CSV/TSV/XLS/XLSX inside a `.twbx` become a downloadable `.xlsx` (via `openpyxl`). Custom SQL is copied to extra sheets and is not executed. Hyper extracts are listed only — they are not converted on Vercel.
- **Inspect Schema:** Datasources, all columns (including hidden), Parameters, connections (passwords redacted), and dashboard wireframes you can Print to PDF.
- **Multi-Interface Support:** Run it as a CLI tool, an offline Streamlit GUI, or a Next.js web application.

## Architecture

```
                       [ .twb / .twbx bytes ]
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │    engine/parser.py  │ (xml.etree + zipfile)
                     └───────────┬──────────┘
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │    engine/schema.py  │ (Pydantic v2 Models)
                     └───────────┬──────────┘
                                 │
                                 ▼
          ┌──────────────────────┼──────────────────────┐
          ▼                      ▼                      ▼
 engine/transpiler.py     engine/extract.py      engine/inspect.py
          │                      │                      │
          └──────────┬───────────┴──────────┬───────────┘
                     ▼                      ▼
              TranspileResult         xlsx bytes
          (calculated_fields, sql_script,
           inspect, extract_inventory, dashboards)
                     │
            ┌────────┼────────┐
            ▼        ▼        ▼
       [ cli.py ] [ app_local.py ] [ /api/transpile + /api/extract ]
```

## Quickstart

### Prerequisites

- Python 3.11+
- Node.js 18+ (Optional, only required if running the Next.js web UI)

### Installation

```bash
# Clone the repository
git clone https://github.com/lukashalim/scapeTableau.git
cd scapeTableau

# Install Python dependencies
pip install -r requirements.txt
```

`openpyxl` is required for Excel extract. `tableauhyperapi` is optional and never required; Hyper files stay listed, not converted, so uploads are never written to disk.

## Usage

### 1. Python API

Use BIEscape directly within your Python scripts or data pipelines:

```python
from engine import transpile

# Transpile from a local file path
result = transpile("path/to/workbook.twbx")

# Or pass raw bytes directly
# result = transpile(file_bytes)

# Print generated ANSI SQL views
print(result.sql_script)

# Inspect individual calculated fields
for field in result.calculated_fields:
    print(f"Field: {field.caption} ({field.datatype})")
    print(f"Original: {field.raw_formula}")
    print(f"ANSI SQL: {field.sql_expression}")
    print(f"Parse Status: {field.status}\n")

# Access Markdown Audit Report
print(result.markdown_audit)

# Extra inspect / extract inventory (new keys; existing JSON keys unchanged)
print(result.inspect.counts if result.inspect else None)
print(result.extract_inventory)
```

JSON still serializes calculated fields as `formula` / `sql` (not `raw_formula` / `sql_expression`).

### 2. Command Line Interface (CLI)

Process workbooks directly from your terminal using `cli.py`:

```bash
# Process a workbook and output summary table to stdout
python cli.py process samples/superstore_calcs.twb --print-summary

# Export SQL, Markdown, and JSON results to an output folder
python cli.py process my_dashboard.twbx --output-dir ./output --format all

# Pipe binary content via stdin (used for web API streaming)
cat my_dashboard.twbx | python cli.py process - --format json

# Inspect schema (JSON or markdown)
python cli.py inspect samples/superstore_calcs.twb --format json
python cli.py inspect samples/superstore_calcs.twb --format markdown

# Extract packaged tables to xlsx
python cli.py extract samples/superstore_calcs.twbx --output extract.xlsx

# Stream xlsx bytes to stdout (web /api/extract)
cat samples/superstore_calcs.twbx | python cli.py extract - > extract.xlsx
```

CLI Options for `process`:

- `--output-dir` / `-o`: Directory to save extracted `.sql`, `.md`, and `.json` artifacts.
- `--format` / `-f`: Choose output format (`sql`, `markdown`, `json`, `all`). Default: `all`.
- `--print-summary`: Displays a clean terminal table of all extracted fields.

### 3. Streamlit Offline GUI

For non-technical users or local air-gapped environments without web access:

```bash
streamlit run app_local.py
```

Workspace tabs: **Transpile to SQL/dbt**, **Extract Data**, **Inspect Schema**. Green "Local Offline Mode" status badge, sidebar SQL dialects, and download buttons. Hyper Excel conversion is not performed (uploads stay in memory).

### 4. Next.js Web App

Run the modern web interface locally:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:4317 in the browser.

- Drag-and-drop target for `.twb` and `.twbx` files.
- "Try with Sample Workbook" button (inspect + layout; the sample `.twb` has no packaged CSV — use `samples/superstore_calcs.twbx` to exercise Excel extract).
- Workspace tabs after a successful upload:
  - **Transpile to SQL/dbt** — SQL / Audit / Markdown
  - **Extract Data** — archive inventory, custom SQL, Hyper index, Download `.xlsx`
  - **Inspect Schema** — connections (redacted), fields with hidden/active filter, parameters, dashboard wireframes + Print
- APIs: `POST /api/transpile` (JSON metadata) and `POST /api/extract` (xlsx bytes). Uploads are never written to disk.

## Running Tests

The test suite validates parser execution, formula tokenization, extract/inspect, password redaction, and edge-case handling against synthetic Tableau XML workbooks:

```bash
pytest engine/test_engine.py
```

## License

Distributed under the MIT License. See LICENSE for more information.
