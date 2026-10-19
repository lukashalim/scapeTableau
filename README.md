# scapeTableau

Instantly Extract & Transpile Tableau Calculated Fields into Clean ANSI SQL & dbt Models.

scapeTableau is a zero-dependency, pure-Python engine designed to automate the painful process of migrating legacy Tableau workbooks (.twb and .twbx) into modern cloud data stacks (Snowflake, BigQuery, Postgres, DuckDB, and dbt).

Parsing and transpilation execute 100% in-memory. Your files are never persisted to disk, uploaded to cloud storage, or sent to external LLM APIs—making it fully compliant with strict corporate data privacy policies.

## Key Features

- **In-Memory & Air-Gapped:** Zero disk writes and zero telemetry. Ideal for sensitive corporate workbook audits.
- **Zero-Dependency Python Engine:** Built entirely on standard Python utilities (`xml.etree` and `zipfile`). Fast, lightweight, and deterministic.
- **Smart AST Formula Transpilation:** Recursively transpiles Tableau-specific syntax (`IIF`, `ZN`, `IFNULL`, `STARTSWITH`, `CONTAINS`, `CASE`) into standard ANSI SQL.
- **Graceful LOD Fallbacks:** Complex Level of Detail (`FIXED`, `INCLUDE`, `EXCLUDE`) calculations are cleanly isolated and commented with `-- TODO: Review LOD logic` rather than crashing.
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
                     ┌──────────────────────┐
                     │ engine/transpiler.py │ (Formula Transpilation)
                     └───────────┬──────────┘
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │   TranspileResult    │
                     └─────┬─────┬─────┬────┘
                           │     │     │
            ┌──────────────┘     │     └──────────────┐
            ▼                    ▼                    ▼
       [ cli.py ]        [ app_local.py ]      [ /api/transpile ]
       (Typer CLI)         (Streamlit)         (Next.js App Router)
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

## Usage

### 1. Python API

Use scapeTableau directly within your Python scripts or data pipelines:

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
```

### 2. Command Line Interface (CLI)

Process workbooks directly from your terminal using `cli.py`:

```bash
# Process a workbook and output summary table to stdout
python cli.py process samples/superstore_calcs.twb --print-summary

# Export SQL, Markdown, and JSON results to an output folder
python cli.py process my_dashboard.twbx --output-dir ./output --format all

# Pipe binary content via stdin (used for web API streaming)
cat my_dashboard.twbx | python cli.py process - --format json
```

CLI Options:

- `--output-dir` / `-o`: Directory to save extracted `.sql`, `.md`, and `.json` artifacts.
- `--format` / `-f`: Choose output format (`sql`, `markdown`, `json`, `all`). Default: `all`.
- `--print-summary`: Displays a clean terminal table of all extracted fields.

### 3. Streamlit Offline GUI

For non-technical users or local air-gapped environments without web access:

```bash
streamlit run app_local.py
```

Features a green "Local Offline Mode" status badge, sidebar selection for SQL dialects (ANSI SQL, Snowflake, PostgreSQL, DuckDB), live interactive field tables, and instant file download buttons.

### 4. Next.js Web App

Run the modern web interface locally:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:4317 in your browser.

- Drag-and-drop target for `.twb` and `.twbx` files.
- "Try with Sample Workbook" button to instantly test logic offline.
- Monaco-style SQL preview with copy-to-clipboard functionality.
- Searchable audit table for calculated fields.

## Running Tests

The test suite validates parser execution, formula tokenization, and edge-case handling against synthetic Tableau XML workbooks:

```bash
pytest engine/test_engine.py
```

## License

Distributed under the MIT License. See LICENSE for more information.
