"""Regression tests for the fixable conversion weak points (tiers A, B and C)."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from engine.pbip import transpile_to_pbip
from engine.pbip.data_csv import collect_data_csvs
from engine.schema import (
    ColumnField,
    ConnectionInfo,
    DatasourceInfo,
    ParseStatus,
    RelationInfo,
    TranspileResult,
)
from engine.transpiler import FormulaContext, transpile_formula

_TWB = """<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'><datasources>
<datasource name='federated.aaa' caption='Orders'>
<connection class='textscan' filename='orders.csv' directory='.'/>
<column name='[Region]' datatype='string' role='dimension'/>
<column name='[Category]' datatype='string' role='dimension'/>
<column name='[Sales]' datatype='real' role='measure'/>
</datasource>
<datasource name='federated.bbb' caption='Vaccines'>
<connection class='textscan' filename='vax.csv' directory='.'/>
<column name='[State]' datatype='string' role='dimension'/>
<column name='[Doses]' datatype='integer' role='measure'/>
</datasource>
</datasources>
<worksheets>
<worksheet name='Vax by State'><table><view>
<datasources><datasource name='federated.aaa'/><datasource name='federated.bbb'/></datasources></view>
<panes><pane><mark class='Bar'/></pane></panes>
<rows>[federated.bbb].[sum:Doses:qk]</rows><cols>[federated.bbb].[none:State:nk]</cols></table></worksheet>
<worksheet name='KPI'><table><view><datasources><datasource name='federated.aaa'/></datasources></view>
<panes><pane><mark class='Automatic'/><encodings><text column='[federated.aaa].[sum:Sales:qk]'/></encodings></pane></panes>
<rows></rows><cols></cols></table></worksheet>
<worksheet name='Pie'><table><view><datasources><datasource name='federated.aaa'/></datasources></view>
<panes><pane><mark class='Pie'/><encodings>
<color column='[federated.aaa].[none:Category:nk]'/>
<wedge-size column='[federated.aaa].[sum:Sales:qk]'/></encodings></pane></panes>
<rows></rows><cols></cols></table></worksheet>
<worksheet name='Heat'><table><view><datasources><datasource name='federated.aaa'/></datasources></view>
<panes><pane><mark class='Square'/><encodings>
<color column='[federated.aaa].[sum:Sales:qk]'/></encodings></pane></panes>
<rows>[federated.aaa].[none:Region:nk]</rows><cols>[federated.aaa].[none:Category:nk]</cols></table></worksheet>
<worksheet name='Sorted'><table><view><datasources><datasource name='federated.aaa'/></datasources>
<computed-sort column='[federated.aaa].[none:Region:nk]' direction='DESC' using='[federated.aaa].[sum:Sales:qk]'/>
</view>
<panes><pane><mark class='Bar'/></pane></panes>
<rows>[federated.aaa].[sum:Sales:qk]</rows><cols>[federated.aaa].[none:Region:nk]</cols></table></worksheet>
</worksheets></workbook>"""


def _convert(tmp_path: Path):
    path = tmp_path / "weak.twb"
    path.write_text(_TWB, encoding="utf-8")
    data, result = transpile_to_pbip(path)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        files = {n: zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".json")}
    return files, result


def _visual_for(files: dict[str, str], sheet: str) -> dict:
    """Locate a visual whose query/title belongs to the named sheet via the status page mapping."""
    status = {s["worksheet"]: s for s in json.loads(files["conversion_status.json"])}
    page_id = status[sheet]["page"]
    visuals = [
        json.loads(v)
        for k, v in files.items()
        if k.endswith("visual.json") and f"/{page_id}/" in k
    ]
    return next(v for v in visuals if v["visual"]["visualType"] != "slicer")


def test_worksheet_binds_to_the_datasource_its_shelves_use(tmp_path: Path) -> None:
    files, _ = _convert(tmp_path)
    visual = _visual_for(files, "Vax by State")
    refs = json.dumps(visual["visual"]["query"])
    assert "Vaccines" in refs
    assert "Doses" in refs and "State" in refs
    assert "Orders" not in refs


def test_text_encoding_sheet_becomes_a_card(tmp_path: Path) -> None:
    files, _ = _convert(tmp_path)
    assert _visual_for(files, "KPI")["visual"]["visualType"] in {"card", "multiRowCard"}


def test_pie_binds_wedge_size_to_values_and_color_to_category(tmp_path: Path) -> None:
    files, _ = _convert(tmp_path)
    visual = _visual_for(files, "Pie")
    qs = visual["visual"]["query"]["queryState"]
    assert visual["visual"]["visualType"] == "pieChart"
    assert qs["Category"]["projections"][0]["field"]["Column"]["Property"] == "Category"
    assert "Sales" in json.dumps(qs["Y"])


def test_square_mark_becomes_matrix_with_heat_formatting(tmp_path: Path) -> None:
    files, _ = _convert(tmp_path)
    visual = _visual_for(files, "Heat")
    assert visual["visual"]["visualType"] == "pivotTable"
    qs = visual["visual"]["query"]["queryState"]
    assert {"Rows", "Columns", "Values"} <= set(qs)
    assert "backColor" in json.dumps(visual["visual"]["objects"])


def test_tableau_sort_becomes_sort_definition(tmp_path: Path) -> None:
    files, _ = _convert(tmp_path)
    sort = _visual_for(files, "Sorted")["visual"]["query"]["sortDefinition"]["sort"][0]
    assert sort["direction"] == "Descending"


def test_status_reports_no_unmapped_fields_for_bound_sheets(tmp_path: Path) -> None:
    files, _ = _convert(tmp_path)
    statuses = {s["worksheet"]: s for s in json.loads(files["conversion_status.json"])}
    for sheet in ("Vax by State", "KPI", "Pie", "Heat"):
        bad = [e for e in statuses[sheet]["elements"] if e["status"] == "Not converted"]
        assert not [e for e in bad if e["element"] in {"Rows", "Columns", "Color", "Label"}], (
            sheet,
            bad,
        )


# --- data CSV: header de-duplication, no demo seed, multi-relation Excel joins -------------


def _field(ds: str, caption: str) -> ColumnField:
    return ColumnField(name=caption, caption=caption, datasource=ds)


def test_missing_real_file_yields_header_only_csv_without_demo_rows(tmp_path: Path) -> None:
    twb = tmp_path / "book.twb"
    twb.write_text("<workbook/>", encoding="utf-8")
    ds = DatasourceInfo(
        name="orders",
        caption="orders",
        connections=[ConnectionInfo(class_name="textscan", filename="missing.csv")],
        relations=[RelationInfo(type="table", table="[missing#csv]")],
        fields=[_field("orders", "Region"), _field("orders", "Category"), _field("orders", "Sales")],
    )
    result = TranspileResult(datasources=[ds], workbook_name="book")
    files, notes, _ = collect_data_csvs(twb, result, table_names={"orders": "orders"})
    text = files["Data/orders.csv"].decode("utf-8-sig").strip().splitlines()
    assert text == ["Region,Category,Sales"]
    assert "West" not in "\n".join(text) and "Furniture" not in "\n".join(text)
    assert notes


def test_case_duplicate_headers_are_collapsed(tmp_path: Path) -> None:
    twb = tmp_path / "book.twb"
    twb.write_text("<workbook/>", encoding="utf-8")
    (tmp_path / "o.csv").write_text("order_id,Region\n1,West\n", encoding="utf-8")
    ds = DatasourceInfo(
        name="o",
        caption="o",
        connections=[ConnectionInfo(class_name="textscan", filename="o.csv")],
        relations=[RelationInfo(type="table", table="[o#csv]")],
        fields=[
            _field("o", "order_id"),
            _field("o", "Order Id"),
            _field("o", "Region"),
            _field("o", "region"),
        ],
    )
    result = TranspileResult(datasources=[ds], workbook_name="book")
    files, _, headers = collect_data_csvs(twb, result, table_names={"o": "o"})
    header = files["Data/o.csv"].decode("utf-8-sig").splitlines()[0].split(",")
    lowered = [h.lower().replace(" ", "").replace("_", "") for h in header]
    assert len(lowered) == len(set(lowered))


def test_multi_relation_excel_join_is_materialised(tmp_path: Path) -> None:
    twb = tmp_path / "book.twb"
    twb.write_text("<workbook/>", encoding="utf-8")
    book = Workbook()
    first = book.active
    first.title = "A"
    first.append(["Key", "Left"])
    first.append([1, "x"])
    first.append([2, "y"])
    second = book.create_sheet("B")
    second.append(["Key", "Right"])
    second.append([1, "p"])
    second.append([2, "q"])
    book.save(tmp_path / "join.xlsx")

    ds = DatasourceInfo(
        name="j",
        caption="j",
        connections=[ConnectionInfo(class_name="excel-direct", filename="join.xlsx")],
        relations=[
            RelationInfo(type="table", table="[A$]"),
            RelationInfo(type="table", table="[B$]"),
        ],
        fields=[_field("j", "Key"), _field("j", "Left"), _field("j", "Right")],
    )
    result = TranspileResult(datasources=[ds], workbook_name="book")
    files, _, _ = collect_data_csvs(twb, result, table_names={"j": "j"})
    text = files["Data/j.csv"].decode("utf-8-sig")
    assert "Left" in text and "Right" in text
    assert "x" in text


# --- Tier C: window / running / rank table calcs -------------------------------------------


def _dax(formula: str, dims: list[str] | None) -> tuple[str, ParseStatus, FormulaContext]:
    ctx = FormulaContext(dialect="ansi", target="dax", dax_table="Orders", window_dims=dims or [])
    out, status, _ = transpile_formula(formula, dialect="dax", target="dax", context=ctx)
    return out, status, ctx


def test_rank_becomes_rankx_over_the_sheet_dimension() -> None:
    out, status, ctx = _dax("RANK(SUM([Sales]))", ["Region"])
    assert status == ParseStatus.ok
    assert out.startswith("RANKX(ALLSELECTED(") and "Region" in out
    assert ctx.window_notes


def test_window_sum_becomes_sumx_over_allselected() -> None:
    out, _, _ = _dax("WINDOW_SUM(SUM([Sales]))", ["Region"])
    assert out.startswith("SUMX(ALLSELECTED(") and "CALCULATE(" in out


def test_running_sum_is_cumulative_filter() -> None:
    out, _, _ = _dax("RUNNING_SUM(SUM([Sales]))", ["Region"])
    assert "VAR __cur = MAX(" in out and "FILTER(ALLSELECTED(" in out and "<= __cur" in out


def test_window_calc_without_sheet_dimension_stays_a_todo() -> None:
    out, _, _ = _dax("WINDOW_SUM(SUM([Sales]))", None)
    assert "SUMX(ALLSELECTED(" not in out


def test_dark_tableau_background_is_not_carried_over() -> None:
    from engine.pbip.pbir import _apply_formatting
    from engine.schema import WorksheetInfo

    def fmt(**kw):
        visual = {"visual": {"visualType": "clusteredBarChart"}}
        _apply_formatting(visual, WorksheetInfo(name="s", **kw), series=None)
        return visual["visual"].get("visualContainerObjects", {})

    dark = fmt(title="T", title_color="#ffffff", background_color="#333333")
    assert "background" not in dark
    assert "fontColor" not in dark["title"][0]["properties"]
    light = fmt(title="T", title_color="#222222", background_color="#f2f2f2")
    assert "background" in light and "fontColor" in light["title"][0]["properties"]


def test_m_text_literal_escapes_hash_the_m_way() -> None:
    from engine.pbip.m_partitions import m_text_literal

    assert m_text_literal('Sy. #34, "Opp"') == '"Sy. #(#)34, ""Opp"""'
    assert "#(hash)" not in m_text_literal("#")


def test_datafolder_partition_reads_quoted_csv() -> None:
    from engine.pbip.m_partitions import _csv_from_datafolder

    m = _csv_from_datafolder("titles.csv", "null")
    assert "QuoteStyle=QuoteStyle.Csv" in m and "QuoteStyle.None" not in m


_KPI_TWB = """<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'><datasources>
<datasource name='federated.h' caption='Hotels'>
<connection class='textscan' filename='h.csv' directory='.'/>
<column name='[Hotel name]' datatype='string' role='dimension'/>
<column name='[country]' caption='Country' datatype='string' role='dimension'/>
<column name='[Score]' datatype='real' role='measure'/>
<column caption='Total Hotels' datatype='string' name='[Calculation_1]' role='dimension' type='nominal'>
<calculation class='tableau' formula='&quot;Total Hotels&quot;' /></column>
</datasource></datasources>
<worksheets>
<worksheet name='KPI'><table><view><datasources><datasource name='federated.h'/></datasources></view>
<panes><pane><mark class='Automatic'/><encodings>
<text column='[federated.h].[none:Calculation_1:nk]'/>
<text column='[federated.h].[ctd:Hotel name:qk]'/></encodings></pane></panes>
<rows></rows><cols></cols></table></worksheet>
<worksheet name='Map'><table><view><datasources><datasource name='federated.h'/></datasources></view>
<panes><pane><mark class='Automatic'/><encodings>
<lod column='[federated.h].[none:country:nk]'/>
<size column='[federated.h].[sum:Score:qk]'/></encodings></pane></panes>
<rows>[federated.h].[Latitude (generated)]</rows><cols>[federated.h].[Longitude (generated)]</cols></table></worksheet>
</worksheets></workbook>"""


def _convert_kpi(tmp_path: Path):
    (tmp_path / "h.csv").write_text("Hotel name,Country,Score\nA,UK,4\nB,UK,3\n", encoding="utf-8")
    path = tmp_path / "kpi.twb"
    path.write_text(_KPI_TWB, encoding="utf-8")
    data, _ = transpile_to_pbip(path)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".json")}


def test_string_literal_label_becomes_card_title(tmp_path: Path) -> None:
    files = _convert_kpi(tmp_path)
    visual = _visual_for(files, "KPI")
    assert visual["visual"]["visualType"] == "card"
    title = visual["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]
    assert title["expr"]["Literal"]["Value"] == "'Total Hotels'"
    assert "Hotel name" in json.dumps(visual["visual"]["query"])


def test_map_detail_resolves_case_insensitively(tmp_path: Path) -> None:
    files = _convert_kpi(tmp_path)
    visual = _visual_for(files, "Map")
    assert visual["visual"]["visualType"] == "map"
    cat = visual["visual"]["query"]["queryState"]["Category"]["projections"][0]
    assert cat["field"]["Column"]["Property"] == "Country"
