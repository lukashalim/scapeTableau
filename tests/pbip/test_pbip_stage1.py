"""Stage 1 PBIP zip shape + TMDL parse + CSV / Postgres / Superstore fixtures."""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from engine import transpile
from engine.pbip import build_pbip_zip, transpile_to_pbip
from engine.pbip.m_partitions import plan_m_partition
from engine.pbip.render import load_template
from engine.schema import ConnectionInfo, DatasourceInfo, ParseStatus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pbip"
SAMPLES = ROOT / "samples"
SUPERSTORE = Path("/cursor/stores/self/internal/US_Superstore_10.0.twbx")
if not SUPERSTORE.is_file():
    SUPERSTORE = SAMPLES / "US_Superstore_10.0.twbx"


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return set(zf.namelist())


def _read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return zf.read(name).decode("utf-8")


def test_templates_exist() -> None:
    assert "compatibilityLevel" in load_template("database.tmdl")
    assert "datasetReference" in load_template("definition.pbir.json")


def test_csv_twb_pbip_shape() -> None:
    zip_bytes, result = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    names = _zip_names(zip_bytes)
    # Find project name prefix
    pbip = next(n for n in names if n.endswith(".pbip"))
    prefix = pbip[: -len(".pbip")]
    sm = f"{prefix}.SemanticModel"
    report = f"{prefix}.Report"
    required = {
        pbip,
        f"{sm}/definition.pbism",
        f"{sm}/definition/database.tmdl",
        f"{sm}/definition/model.tmdl",
        f"{sm}/definition/relationships.tmdl",
        f"{sm}/definition/expressions.tmdl",
        f"{report}/definition.pbir",
        f"{report}/definition/version.json",
        f"{report}/definition/report.json",
        f"{report}/definition/pages/pages.json",
        "CONVERSION_REPORT.md",
    }
    assert required <= names
    assert any(n.startswith(f"{sm}/definition/tables/") and n.endswith(".tmdl") for n in names)

    pbir = json.loads(_read(zip_bytes, f"{report}/definition.pbir"))
    assert pbir["datasetReference"]["byPath"]["path"] == f"../{sm}"

    model = _read(zip_bytes, f"{sm}/definition/model.tmdl")
    assert "ref table" in model
    # Small CSVs are embedded in M (Custom SQL / Postgres must not stay live)
    tables = [n for n in names if "/tables/" in n and n.endswith(".tmdl")]
    blob = "\n".join(_read(zip_bytes, n) for n in tables)
    assert "Text.ToBinary" in blob
    assert "Csv.Document" in blob
    assert "PostgreSQL.Database" not in blob
    assert "DataFolder" not in blob
    assert "CONVERSION_REPORT.md" in names
    report_md = _read(zip_bytes, "CONVERSION_REPORT.md")
    assert "converted" in report_md.lower()
    assert result.calculated_fields


def test_postgres_custom_sql_uses_datafolder_csv() -> None:
    """Desktop-open PBIP must not prompt for Postgres on West Custom SQL."""
    result = transpile(SAMPLES / "superstore_calcs.twb")
    west = next(ds for ds in result.datasources if (ds.caption or "") == "West Custom SQL")
    assert any((c.class_name or "").lower() == "postgres" for c in west.connections)
    assert any(r.custom_sql for r in west.relations)
    zip_bytes = build_pbip_zip(result, source=SAMPLES / "superstore_calcs.twb")
    names = _zip_names(zip_bytes)
    blob = b"".join(
        zipfile.ZipFile(BytesIO(zip_bytes)).read(n)
        for n in names
        if n.endswith(".tmdl")
    ).decode("utf-8", errors="replace")
    assert "PostgreSQL.Database" not in blob
    assert "Text.ToBinary" in blob
    assert "West,100,20" in blob
    west_csv = next(n for n in names if n.endswith("West Custom SQL.csv"))
    west_body = zipfile.ZipFile(BytesIO(zip_bytes)).read(west_csv).decode("utf-8-sig")
    assert "West" in west_body
    assert "Profit" in west_body.splitlines()[0] or "Sales" in west_body.splitlines()[0]


def _orders_ds() -> DatasourceInfo:
    return DatasourceInfo(
        name="Orders",
        caption="Orders",
        connections=[ConnectionInfo(class_name="textscan", filename="Orders.csv")],
    )


def test_small_csv_is_embedded_not_postgres() -> None:
    plan = plan_m_partition(
        _orders_ds(),
        table_name="Orders",
        column_types=[("Region", "type text"), ("Sales", "type number")],
        csv_filename="Orders.csv",
        csv_text='Region,Sales\nWest,"1,000"\n',
    )
    assert "Text.ToBinary" in plan.expression
    assert "PostgreSQL.Database" not in plan.expression
    assert "DataFolder" not in plan.expression
    assert 'West,""1,000""' in plan.expression
    assert plan.parameters == []


def test_large_csv_uses_datafolder() -> None:
    plan = plan_m_partition(
        _orders_ds(),
        table_name="Orders",
        column_types=[("Region", "type text")],
        csv_filename="Orders.csv",
        csv_text="Region\n" + ("West\n" * 40),
        inline_max_chars=20,
    )
    assert "DataFolder" in plan.expression
    assert "Text.ToBinary" not in plan.expression
    assert "PostgreSQL.Database" not in plan.expression


def test_join_fixture_relationships() -> None:
    zip_bytes, result = transpile_to_pbip(FIXTURES / "join_demo.twb")
    assert result.joins, "expected join parse from fixture"
    rel_path = next(n for n in _zip_names(zip_bytes) if n.endswith("relationships.tmdl"))
    rel = _read(zip_bytes, rel_path)
    # Both join sides live in one flat table; Desktop rejects self-relationships.
    for block in rel.split("relationship ")[1:]:
        from_col = re.search(r"fromColumn: (.+)\.", block)
        to_col = re.search(r"toColumn: (.+)\.", block)
        assert from_col and to_col and from_col.group(1) != to_col.group(1)
    # No leading empty line (Desktop: InvalidLineType Empty!)
    assert not rel.startswith("\n")
    assert "///" not in rel


def test_same_table_join_is_not_emitted_as_relationship() -> None:
    """Desktop: "table has a relationship to itself ... not supported"."""
    from engine.pbip.package import _model_joins, _relationships_tmdl
    from engine.schema import JoinInfo

    join = JoinInfo(
        left_datasource="Orders",
        left_column="Order ID",
        right_datasource="Returns",
        right_column="Order ID",
    )
    flat = {"Orders": "Sample - Superstore", "Returns": "Sample - Superstore"}
    usable, skipped = _model_joins([join], flat)
    assert usable == [] and skipped == [join]
    assert _relationships_tmdl([join], flat) == ""

    split = {"Orders": "Orders", "Returns": "Returns"}
    assert "relationship Relationship1" in _relationships_tmdl([join], split)


def test_typed_csv_query_is_well_formed_m() -> None:
    """A stray quote after ``Typed`` made Desktop fail with "Token ';' expected"."""
    from engine.pbip.m_partitions import _typed_csv_query

    transforms = '{{"A", type text}, {"B", Int64.Type}}'
    for query in (
        _typed_csv_query('Csv.Document(File.Contents("x.csv"))', transforms),
        _typed_csv_query('Csv.Document(File.Contents("x.csv"))', "null"),
    ):
        assert query.count('"') % 2 == 0, query
        assert query.rstrip().splitlines()[-1].strip() in {"Typed", '#"Replaced Blanks"'}
        assert "MissingField.UseNull" in query or "Typed" not in query


def test_filled_map_worksheet_maps_to_filled_map_with_filter() -> None:
    """Tableau Multipolygon map -> filledMap (State as Location) + Region member filter."""
    from engine.parser import parse_workbook

    twb = """<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'><datasources>
<datasource name='Orders' caption='Orders'>
<connection class='textscan' filename='o.csv' directory='.'/>
<column name='[State]' datatype='string' role='dimension'/>
<column name='[Region]' datatype='string' role='dimension'/>
<column name='[Sales]' datatype='real' role='measure'/>
</datasource></datasources>
<worksheets><worksheet name='Central Map'><table><view>
<datasources><datasource name='Orders'/></datasources>
<filter class='categorical' column='[Orders].[none:Region:nk]'>
<groupfilter function='member' level='[none:Region:nk]' member='&quot;Central&quot;'/>
</filter></view>
<panes><pane><mark class='Multipolygon'/><encodings>
<lod column='[Orders].[none:State:nk]'/>
<color column='[Orders].[sum:Sales:qk]'/><tooltip column='[Orders].[none:Region:nk]'/></encodings></pane></panes>
<rows>[Orders].[Latitude (generated)]</rows><cols>[Orders].[Longitude (generated)]</cols>
</table></worksheet></worksheets></workbook>"""
    path = Path(__file__).parent / "_tmp_map.twb"
    path.write_text(twb, encoding="utf-8")
    try:
        zip_bytes, result = transpile_to_pbip(path)
    finally:
        path.unlink(missing_ok=True)
    assert result.worksheets[0].filters == {"Region": ["Central"]}
    assert result.worksheets[0].detail_fields == ["State"]
    visual_path = next(n for n in _zip_names(zip_bytes) if n.endswith("visual.json"))
    visual = json.loads(_read(zip_bytes, visual_path))
    assert visual["visual"]["visualType"] == "filledMap"
    location = visual["visual"]["query"]["queryState"]["Category"]["projections"][0]
    assert location["field"]["Column"]["Property"] == "State"
    flt = visual["filterConfig"]["filters"][0]
    assert flt["field"]["Column"]["Property"] == "Region"
    where = flt["filter"]["Where"][0]["Condition"]["In"]["Values"]
    assert where == [[{"Literal": {"Value": "'Central'"}}]]

    # Per-element status report
    status = json.loads(_read(zip_bytes, "conversion_status.json"))[0]
    rows = {(e["element"], e["tableau"]): e["status"] for e in status["elements"]}
    assert rows[("Visual type", "Multipolygon mark")] == "Full"
    assert rows[("Detail / Location", "Detail: State")] == "Full"
    assert rows[("Filter", "Region = Central")] == "Full"
    # The colour measure now drives the map's fill value role.
    assert rows[("Color", "Color: Sales")] == "Full"
    assert rows[("Formatting", "Colors, fonts, mark styles, axes, title")] == "Not converted"
    md = _read(zip_bytes, "CONVERSION_STATUS.md")
    assert "## Central Map" in md


def test_empty_relationships_tmdl_is_truly_empty() -> None:
    """Desktop rejects relationships.tmdl with comment + blank line (Empty! at line 2)."""
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    rel_path = next(n for n in _zip_names(zip_bytes) if n.endswith("relationships.tmdl"))
    rel = _read(zip_bytes, rel_path)
    assert rel == "", repr(rel)


def test_empty_expressions_tmdl_is_truly_empty() -> None:
    """Desktop rejects expressions.tmdl with a comment and a blank line 2."""
    zip_bytes, _ = transpile_to_pbip(FIXTURES / "join_demo.twb")
    expr_path = next(n for n in _zip_names(zip_bytes) if n.endswith("expressions.tmdl"))
    expr = _read(zip_bytes, expr_path)
    assert expr == "", repr(expr)


def test_measures_tmdl_has_no_consecutive_blank_lines() -> None:
    """Desktop rejects Empty! on a blank line inside __Measures (line 5 of the stub)."""
    zip_bytes, _ = transpile_to_pbip(FIXTURES / "join_demo.twb")
    path = next(n for n in _zip_names(zip_bytes) if n.endswith("__Measures.tmdl"))
    text = _read(zip_bytes, path)
    assert "no measures" not in text
    for name in _zip_names(zip_bytes):
        if not name.endswith(".tmdl"):
            continue
        lines = _read(zip_bytes, name).splitlines()
        for index in range(1, len(lines)):
            assert lines[index].strip() or lines[index - 1].strip(), (
                name,
                index + 1,
            )


@pytest.mark.skipif(not SUPERSTORE.is_file(), reason="US_Superstore_10.0.twbx missing")
def test_superstore_pbip_golden_shape() -> None:
    zip_bytes, result = transpile_to_pbip(SUPERSTORE)
    names = _zip_names(zip_bytes)
    assert "CONVERSION_REPORT.md" in names
    assert any(n.endswith(".pbip") for n in names)
    # Hyper → CSV placeholder TODO
    report = _read(zip_bytes, "CONVERSION_REPORT.md")
    assert "Hyper" in report or "hyper" in report.lower()
    captions = {f.caption: f for f in result.calculated_fields}
    assert "Profit Ratio" in captions
    assert captions["Profit Ratio"].dax_status == ParseStatus.ok
    assert "DIVIDE(" in (captions["Profit Ratio"].dax or "")
    # Measures table contains Profit Ratio
    measures_file = next(n for n in names if n.endswith("__Measures.tmdl"))
    measures = _read(zip_bytes, measures_file)
    assert "Profit Ratio" in measures
    assert "Profit per Customer" in measures
    assert "Achieved Quota" in measures or any(
        f.caption == "Achieved Quota" and f.dax_kind == "measure" for f in result.calculated_fields
    )


_TMDL_OBJECT = re.compile(r"^(table|column|measure|partition|relationship|expression|ref|database|model)\b", re.M)


def test_custom_sql_object_count_becomes_filled_map_and_bars(tmp_path: Path) -> None:
    """Live custom SQL: metadata columns, CNT(table) as COUNTROWS, geometry as a filled map.

    Tableau often omits a ``<column>`` for query outputs and counts the object-model
    table (``datatype='table'``) instead of a field. That is the businesses map:
    polygons coloured by row count, and a bar of industry by that same count.
    """

    twb = """<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'>
<datasources>
  <datasource name='federated.m' caption='businesses (postgres)'>
    <connection class='federated'>
      <named-connections>
        <named-connection name='postgres.1'>
          <connection class='postgres' server='db.example.com' dbname='postgres' port='5432'/>
        </named-connection>
      </named-connections>
      <relation name='Custom SQL Query' type='text' connection='postgres.1'>select b.state, cc.display_name as category_name, cg.label as industry from businesses b</relation>
      <metadata-records>
        <metadata-record class='column'>
          <remote-name>state</remote-name>
          <local-name>[state]</local-name>
          <local-type>string</local-type>
        </metadata-record>
        <metadata-record class='column'>
          <remote-name>category_name</remote-name>
          <local-name>[category_name]</local-name>
          <local-type>string</local-type>
        </metadata-record>
        <metadata-record class='column'>
          <remote-name>industry</remote-name>
          <local-name>[industry]</local-name>
          <local-type>string</local-type>
        </metadata-record>
      </metadata-records>
    </connection>
    <column caption='Category' datatype='string' name='[Calculation_1]' role='dimension' type='nominal'>
      <calculation class='tableau' formula='IFNULL([category_name],&apos;Other&apos;)'/>
    </column>
    <column caption='Custom SQL Query' datatype='table' name='[__tableau_internal_object_id__].[_ABCDEF]' role='measure' type='quantitative'/>
    <column caption='State' datatype='string' name='[state]' role='dimension' semantic-role='[State].[Name]' type='nominal'/>
  </datasource>
</datasources>
<dashboards>
  <dashboard name='Dashboard 1'>
    <size maxheight='800' maxwidth='1000'/>
    <zones>
      <zone h='100000' w='100000' x='0' y='0' type-v2='layout-basic'>
        <zone h='8000' w='98000' x='1000' y='1000' type-v2='text'>
          <formatted-text><run fontsize='16'>Businesses Without Websites</run></formatted-text>
        </zone>
        <zone h='60000' w='98000' x='1000' y='10000' name='Sheet 1'/>
        <zone h='25000' w='98000' x='1000' y='72000' name='Sheet 2'/>
      </zone>
    </zones>
  </dashboard>
</dashboards>
<worksheets>
  <worksheet name='Sheet 1'><table><view>
    <datasources><datasource name='federated.m'/></datasources>
  </view>
  <panes><pane><mark class='Automatic'/><encodings>
    <lod column='[federated.m].[none:state:nk]'/>
    <color column='[federated.m].[__tableau_internal_object_id__].[cnt:_ABCDEF:qk]'/>
    <geometry column='[federated.m].[Geometry (generated)]'/>
  </encodings></pane></panes>
  <rows>[federated.m].[Latitude (generated)]</rows>
  <cols>[federated.m].[Longitude (generated)]</cols>
  </table></worksheet>
  <worksheet name='Sheet 2'><table><view>
    <datasources><datasource name='federated.m'/></datasources>
    <shelf-sorts>
      <shelf-sort-v2 dimension-to-sort='[federated.m].[none:industry:nk]' direction='DESC' measure-to-sort-by='[federated.m].[__tableau_internal_object_id__].[cnt:_ABCDEF:qk]' shelf='rows'/>
    </shelf-sorts>
  </view>
  <panes><pane><mark class='Automatic'/><style><style-rule element='mark'>
    <format attr='mark-labels-show' value='true'/>
  </style-rule></style></pane></panes>
  <rows>[federated.m].[none:industry:nk]</rows>
  <cols>[federated.m].[__tableau_internal_object_id__].[cnt:_ABCDEF:qk]</cols>
  </table></worksheet>
</worksheets>
</workbook>"""
    path = tmp_path / "map.twb"
    path.write_text(twb, encoding="utf-8")
    zip_bytes, result = transpile_to_pbip(path)
    captions = {c.caption for c in result.calculated_fields}
    assert "Count of Custom SQL Query" in captions
    count = next(c for c in result.calculated_fields if c.caption == "Count of Custom SQL Query")
    assert count.dax == "COUNTROWS('businesses (postgres)')"
    assert count.dax_kind == "measure"
    category = next(c for c in result.calculated_fields if c.caption == "Category")
    assert "category_name" in (category.dax or "")
    assert "BLANK()" not in (category.dax or "")
    ds = result.datasources[0]
    physical = {f.caption for f in ds.fields if not f.calculated}
    assert "category_name" in physical and "industry" in physical
    assert "Custom SQL Query" not in physical
    assert result.worksheets[0].has_geometry is True

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        visuals = [
            json.loads(zf.read(n))
            for n in zf.namelist()
            if n.endswith("visual.json")
        ]
        pages = json.loads(next(zf.read(n) for n in zf.namelist() if n.endswith("pages/pages.json")))
        page_blobs = [
            json.loads(zf.read(n))
            for n in zf.namelist()
            if n.endswith("page.json")
        ]
        table = next(
            zf.read(n).decode("utf-8")
            for n in zf.namelist()
            if n.endswith(".tmdl") and "/tables/" in n and "Measures" not in n
        )
    assert "Custom SQL Query" not in table.split("measure")[0]
    assert "dataCategory: StateOrProvince" in table
    assert "column category_name" in table
    types = {v["visual"]["visualType"] for v in visuals}
    assert "filledMap" in types
    assert "clusteredBarChart" in types
    assert "textbox" in types
    filled = next(v for v in visuals if v["visual"]["visualType"] == "filledMap")
    loc = filled["visual"]["query"]["queryState"]["Category"]["projections"][0]
    assert loc["field"]["Column"]["Property"] == "State"
    color = filled["visual"]["query"]["queryState"]["Y"]["projections"][0]
    assert color["field"]["Measure"]["Property"] == "Count of Custom SQL Query"
    bars = next(v for v in visuals if v["visual"]["visualType"] == "clusteredBarChart")
    bar_cat = bars["visual"]["query"]["queryState"]["Category"]["projections"][0]
    assert bar_cat["field"]["Column"]["Property"] == "industry"
    assert bars["visual"]["query"]["sortDefinition"]["sort"][0]["direction"] == "Descending"
    assert "labels" in bars["visual"]["objects"]
    assert any(p.get("displayName") == "Dashboard 1" for p in page_blobs)
    assert len(pages["pageOrder"]) == 1


def test_tmdl_parse_smoke() -> None:
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    for name in _zip_names(zip_bytes):
        if not name.endswith(".tmdl"):
            continue
        text = _read(zip_bytes, name)
        # Minimal structural parse: objects present, no unresolved placeholders
        assert "${" not in text  # unresolved template placeholders
        # relationships.tmdl may be truly empty (no joins) — Desktop requires that
        if name.endswith("relationships.tmdl") and text == "":
            continue
        assert _TMDL_OBJECT.search(text) or text.strip().startswith("///")
