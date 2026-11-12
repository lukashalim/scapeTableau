# Third-party libraries for PBIP / SQL migration

BIEscape constraints: **no-upload** parsing, **Vercel serverless** must stay small
(no Hyper / DuckDB / pantab in the serverless bundle), **desktop Hyper isolation**
via optional `tableauhyperapi`. Copilot CLI is **not** incorporated.

## Incorporation matrix

| Library | Engine | Desktop | Web / Vercel | Decision |
| --- | --- | --- | --- | --- |
| **sqlglot** ([tobymao/sqlglot](https://github.com/tobymao/sqlglot)) | **Used** — post-process generated SQL views to Snowflake / BigQuery / T-SQL; parse Custom SQL in Tableau relations for notes | Same Python path in sidecar CLI | **Used** when `--dialect` / form dialect is set (pure Python, small); `sqlglot.js` browser optional later, not required | Ship in `requirements.txt`; CLI `--dialect` includes `bigquery`; web Transpile accepts `dialect` form field |
| **DuckDB** ([duckdb/duckdb](https://github.com/duckdb/duckdb)) | **Tests only** — default numeric check harness on extracted CSV/XLSX (and Hyper locally) | Not bundled for conversion UI | **Not on Vercel** (size) | Already in `requirements.txt` for pytest; see `tests/harness/duckdb_check.py` |
| **microsoft/json-schemas** ([microsoft/json-schemas](https://github.com/microsoft/json-schemas)) | Vendored under `vendor/microsoft-json-schemas/`; pytest validates Superstore `.pbip` JSON | N/A | N/A | Fail CI on schema violations |
| **Tabular Editor 2** ([TabularEditor/TabularEditor](https://github.com/TabularEditor/TabularEditor)) | N/A | **Do not bundle** | N/A | Optional local Windows validation — docs + `scripts/validate_te2.ps1`. No GitHub Actions Windows job (GUI/license weight); script hook is enough |
| **tableaudocumentapi** ([tableau/document-api-python](https://github.com/tableau/document-api-python)) | **Not used** in runtime. Optional pytest cross-check of field names vs `engine/parser.py` — **skipped** when install fails or workbook XML lacks worksheet `@name` (fights our samples) | N/A | N/A | Does **not** replace `engine/parser.py` (no calcs/worksheets depth) |
| **pantab** + **tableauhyperapi** ([innobi/pantab](https://github.com/innobi/pantab)) | Hyper path stays `engine/hyper.py` + optional `tableauhyperapi` | Same local-only gate | **Never on Vercel** | pantab evaluated: needs pyarrow/numpy stack that fights pinned pandas/numpy here; **not adopted**. Keep tableauhyperapi export |
| **pbir-utils** ([akhilannan/pbir-utils](https://github.com/akhilannan/pbir-utils)) | **Not depended on** | N/A | N/A | Stage 3 visuals use vendored Franssens `pbi.kb` / `tableEx` patterns; pbir-utils does not simplify our emitter enough to take a live dependency |
| **Franssens tableau-to-powerbi-migration** (MIT) | Vendored sparse TMDL/PBIR/visual templates + DAX guide | Patterns only | Patterns only | See `vendor/franssens-tableau-to-powerbi/NOTICE` — **no Copilot CLI** |
| **cyphou/TableauToPowerBI** (MIT) | **Adapted ideas only** — Desktop path budget, `==`→`=` DAX heal, TMDL `''` validation | Same | Same | **No CLI dependency**. Attribution in `THIRD_PARTY_NOTICES`. Repo was renamed from `Tableau-To-PowerBI`. |

## cyphou/TableauToPowerBI comparison (what we ported)

Upstream is a full extract→JSON→generate CLI (`migrate.py`) with openability/heal stacks.
BIEscape stays single-pass AST transpile + in-memory PBIP zip for the web app.

| Area | Theirs | Ours | Ported? |
| --- | --- | --- | --- |
| `.pbip` `$schema` | Emits `$schema` | **Omits** (Desktop refuses) | Keep ours |
| `.platform` / pbism 4.2 / pbir 4.0 | Yes | Yes | Already had |
| BaseTheme file | References `CY26SU04` **without** writing the JSON | Vendors + writes `CY24SU10.json` | Keep ours (stricter) |
| Path budget (260/248) | `desktop_paths.py` | `engine/pbip/desktop_paths.py` | **Yes** |
| DAX `==` → `=` | `dax_healing` | `heal_double_equals` in `dax_polish` | **Yes** |
| LOD / table calcs / MN-MV | Broader WINDOW/RUNNING/quick-calc map | AST LOD + table_calcs + MN skip/expand notes | Partial — not their CLI |
| Hyper | Optional HyperAPI → sample M `#table` | Desktop `tableauhyperapi`; serverless TODO CSV | Keep isolation |
| Visual map | 190-type matrix + custom GUIDs | Franssens subset + mark-class map | Not bulk-ported |

## Franssens vendor set

Under `vendor/franssens-tableau-to-powerbi/`:

- Semantic: `definition.pbism` (4.2), `database.tmdl` (compat **1702**), what-if GENERATESERIES example
- Report: PBIR `definition/` (`version.json` 2.0.0, `pages/`, `report.json`)
- Visuals: card, pie, treemap, waterfall, stacked column, combo, etc. + simple `tableEx`
- Docs: `tableau-dax-translation-guide.md` (compact-filter VAR hoist and related gotchas)

Runtime filler: `engine/pbip/` (templates under `engine/pbip/templates/` aligned to these patterns).

## Optional Tabular Editor 2

On Windows, after unzipping a `.pbip.zip`:

```powershell
pwsh scripts/validate_te2.ps1 -PbipDir .\MyWorkbook
```

Requires a local Tabular Editor 2 install. Not run in CI.
