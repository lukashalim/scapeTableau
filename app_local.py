"""Air-gapped Streamlit wrapper around the EscapeTableau engine."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import transpile
from engine.schema import WorkbookParseError

st.set_page_config(page_title="EscapeTableau — local", page_icon="▦", layout="wide")

DIALECTS = ("ansi", "snowflake", "postgres", "duckdb")

st.markdown(
    """
    <div style="background:#0f7b3a;color:#fff;padding:0.75rem 1rem;border-radius:0.5rem;font-weight:600;">
      Local Offline Mode — workbooks stay in this process. Nothing is uploaded or written unless you download.
    </div>
    """,
    unsafe_allow_html=True,
)

st.title("EscapeTableau")
st.caption("Convert Tableau calculated fields to SQL on this machine.")

with st.sidebar:
    st.header("Dialect")
    dialect = st.selectbox("Target SQL dialect", DIALECTS, index=0)
    st.caption(
        "Snowflake, Postgres, and DuckDB use ILIKE for CONTAINS / STARTSWITH. "
        "Identifiers stay ANSI-quoted."
    )
    st.divider()
    st.markdown("Run the CLI for batch jobs: `python cli.py process workbook.twb --output-dir ./output`")

uploaded = st.file_uploader(
    "Tableau workbook",
    type=["twb", "twbx"],
    help="Parsed in memory. The file is not saved to disk.",
)

if uploaded is None:
    st.info("Upload a .twb or .twbx to transpile calculated fields.")
    st.stop()

payload = uploaded.getvalue()
try:
    result = transpile(payload, dialect=dialect)
except WorkbookParseError as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:
    st.error(f"Conversion failed: {exc}")
    st.stop()

ok = sum(1 for f in result.calculated_fields if f.status.value == "ok")
st.success(
    f"{uploaded.name}: {len(result.calculated_fields)} calculated fields "
    f"({ok} ok) across {len(result.datasources)} datasources."
)

sql_tab, table_tab, download_tab = st.tabs(["SQL", "Dataframe", "Downloads"])

with sql_tab:
    st.code(result.sql_script, language="sql")

with table_tab:
    frame = pd.DataFrame(
        [
            {
                "caption": f.caption,
                "datasource": f.datasource,
                "role": f.role,
                "datatype": f.datatype,
                "status": f.status.value,
                "formula": f.formula,
                "sql": f.sql,
                "error": f.error,
            }
            for f in result.calculated_fields
        ]
    )
    if frame.empty:
        st.warning("No calculated fields found in this workbook.")
    else:
        st.dataframe(frame, use_container_width=True)

with download_tab:
    st.download_button(
        "Download SQL",
        data=result.sql_script.encode("utf-8"),
        file_name="escapetableau.sql",
        mime="text/sql",
    )
    st.download_button(
        "Download audit Markdown",
        data=result.markdown_audit.encode("utf-8"),
        file_name="escapetableau-audit.md",
        mime="text/markdown",
    )
    st.download_button(
        "Download JSON",
        data=result.model_dump_json(indent=2).encode("utf-8"),
        file_name="escapetableau.json",
        mime="application/json",
    )
