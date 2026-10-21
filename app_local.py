"""Air-gapped Streamlit wrapper around the EscapeTableau engine."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import extract_to_xlsx, transpile
from engine.schema import WorkbookParseError

st.set_page_config(page_title="EscapeTableau — local", page_icon="▦", layout="wide")

DIALECTS = ("ansi", "snowflake", "postgres", "duckdb", "tsql")

st.markdown(
    """
    <div style="background:#0f7b3a;color:#fff;padding:0.75rem 1rem;border-radius:0.5rem;font-weight:600;">
      Local Offline Mode — workbooks stay in this process. Nothing is uploaded or written unless you download.
    </div>
    """,
    unsafe_allow_html=True,
)

st.title("EscapeTableau")
st.caption("Convert Tableau calculated fields to SQL, extract packaged tables, and inspect schema on this machine.")

with st.sidebar:
    st.header("Dialect")
    dialect = st.selectbox("Target SQL dialect", DIALECTS, index=0)
    st.caption(
        "Snowflake, Postgres, and DuckDB use ILIKE for CONTAINS / STARTSWITH. "
        "Identifiers stay ANSI-quoted."
    )
    st.divider()
    st.markdown("Run the CLI for batch jobs: `python cli.py process workbook.twb --output-dir ./output`")
    st.markdown("`python cli.py inspect workbook.twb` · `python cli.py extract workbook.twbx -o out.xlsx`")

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

transpile_tab, extract_tab, inspect_tab = st.tabs(
    ["Transpile to SQL/dbt", "Extract Data", "Inspect Schema"]
)

with transpile_tab:
    sql_tab, table_tab, download_tab = st.tabs(["SQL", "Dataframe", "Downloads"])
    with sql_tab:
        st.code(result.sql_script, language="sql")
        if result.dax_script:
            st.subheader("DAX")
            st.code(result.dax_script, language="dax")
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
                    "dax": f.dax,
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
            "Download DAX",
            data=(result.dax_script or "").encode("utf-8"),
            file_name="escapetableau.dax",
            mime="text/plain",
        )
        st.download_button(
            "Download JSON",
            data=result.model_dump_json(indent=2).encode("utf-8"),
            file_name="escapetableau.json",
            mime="application/json",
        )

with extract_tab:
    inv = result.extract_inventory
    if inv is None:
        st.info("No inventory.")
    else:
        for note in inv.notes:
            st.caption(note)
        if inv.archive_members:
            st.dataframe(
                [{"path": m.path, "kind": m.kind, "size": m.size} for m in inv.archive_members],
                use_container_width=True,
            )
        else:
            st.info("Plain .twb — no packaged CSV/XLSX. Custom SQL is still exported as a sheet.")
        if inv.hyper_files:
            from engine.hyper import hyper_conversion_enabled

            if hyper_conversion_enabled():
                st.info(
                    "Hyper extracts convert locally via optional tableauhyperapi "
                    "(temp file for hyperd, deleted after read; 100k row cap). Not used on Vercel."
                )
            else:
                st.warning(
                    "Hyper files are listed only here. "
                    "pip install tableauhyperapi to convert locally (skipped when VERCEL is set)."
                )
        xlsx, _ = extract_to_xlsx(payload)
        st.download_button(
            "Download .xlsx",
            data=xlsx,
            file_name="scapetableau-extract.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with inspect_tab:
    snap = result.inspect
    if snap is None:
        st.info("No inspect snapshot.")
    else:
        st.json(snap.counts.model_dump())
        st.subheader("Connections")
        st.dataframe([c.model_dump() for c in snap.connections], use_container_width=True)
        st.subheader("Fields")
        st.dataframe([f.model_dump() for f in snap.fields], use_container_width=True)
        st.subheader("Parameters")
        st.dataframe([p.model_dump() for p in snap.parameters], use_container_width=True)
        st.subheader("Dashboards")
        for dash in snap.dashboards:
            st.markdown(f"**{dash.name}**")
            if dash.svg:
                st.markdown(dash.svg, unsafe_allow_html=True)
