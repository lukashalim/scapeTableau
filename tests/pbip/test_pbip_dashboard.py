"""Dashboards become positioned pages; line / scatter marks get real chart builders."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from engine.pbip import transpile_to_pbip

_TWB = """<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'><datasources>
<datasource name='Orders' caption='Orders'>
<connection class='textscan' filename='o.csv' directory='.'/>
<column name='[Order Date]' datatype='date' role='dimension'/>
<column name='[Region]' datatype='string' role='dimension'/>
<column name='[Segment]' datatype='string' role='dimension'/>
<column name='[Sales]' datatype='real' role='measure'/>
<column name='[Profit]' datatype='real' role='measure'/>
</datasource></datasources>
<worksheets>
<worksheet name='Trend'><table><view><datasources><datasource name='Orders'/></datasources></view>
<panes><pane><mark class='Line'/><encodings><color column='[Orders].[none:Segment:nk]'/></encodings></pane></panes>
<rows>[Orders].[sum:Sales:qk]</rows><cols>[Orders].[none:Order Date:ok]</cols></table></worksheet>
<worksheet name='Scatter'><table><view><datasources><datasource name='Orders'/></datasources></view>
<panes><pane><mark class='Circle'/><encodings><lod column='[Orders].[none:Region:nk]'/></encodings></pane></panes>
<rows>[Orders].[sum:Profit:qk]</rows><cols>[Orders].[sum:Sales:qk]</cols></table></worksheet>
<worksheet name='Go Button'><table><view><datasources><datasource name='Orders'/></datasources></view>
<panes><pane><mark class='Automatic'/></pane></panes><rows></rows><cols></cols></table></worksheet>
<worksheet name='Loose'><table><view><datasources><datasource name='Orders'/></datasources></view>
<panes><pane><mark class='Bar'/></pane></panes>
<rows>[Orders].[sum:Sales:qk]</rows><cols>[Orders].[none:Region:nk]</cols></table></worksheet>
</worksheets>
<dashboards><dashboard name='Overview'><size sizing-mode='automatic' /><zones>
<zone h='10000' id='1' type-v2='text' w='100000' x='0' y='0'>
<formatted-text><run fontcolor='#ffffff' fontsize='20'>Sales overview</run></formatted-text>
<zone-style><format attr='background-color' value='#34657f' /></zone-style></zone>
<zone h='40000' id='2' name='Trend' w='50000' x='0' y='20000'/>
<zone h='40000' id='3' name='Scatter' w='50000' x='50000' y='20000'/>
<zone h='6000' id='4' name='Go Button' w='10000' x='90000' y='0'/>
<zone h='5000' id='5' param='[Parameters].[P1]' type-v2='paramctrl' w='10000' x='0' y='12000'/>
</zones></dashboard></dashboards></workbook>"""


def _convert() -> tuple[dict[str, str], list]:
    path = Path(__file__).parent / "_tmp_dashboard.twb"
    path.write_text(_TWB, encoding="utf-8")
    try:
        data, result = transpile_to_pbip(path)
    finally:
        path.unlink(missing_ok=True)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        files = {n: zf.read(n).decode("utf-8") for n in zf.namelist() if not n.endswith(".csv")}
    return files, result.worksheets


def test_dashboard_becomes_one_positioned_page() -> None:
    files, _ = _convert()
    pages = json.loads(next(v for k, v in files.items() if k.endswith("pages/pages.json")))
    # Overview page + the sheet that is on no dashboard ("Loose"); sheets on the dashboard get no page
    assert len(pages["pageOrder"]) == 2

    page_json = next(v for k, v in files.items() if k.endswith("page.json") and "Overview" in v)
    page = json.loads(page_json)
    assert (page["width"], page["height"]) == (1920, 1080)

    visuals = [
        json.loads(v)
        for k, v in files.items()
        if k.endswith("visual.json") and f"/{page['name']}/" in k
    ]
    by_type: dict[str, list[dict]] = {}
    for v in visuals:
        by_type.setdefault(v["visual"]["visualType"], []).append(v)

    assert {"lineChart", "scatterChart", "shape", "textbox", "pageNavigator"} <= set(by_type)

    # Trend: x=0, y=20% of 1080, half the page wide
    line = by_type["lineChart"][0]
    assert line["position"]["x"] == 0
    assert line["position"]["y"] == 216
    assert line["position"]["width"] == 960
    qs = line["visual"]["query"]["queryState"]
    assert qs["Category"]["projections"][0]["field"]["Column"]["Property"] == "Order Date"
    assert qs["Series"]["projections"][0]["field"]["Column"]["Property"] == "Segment"
    assert "Aggregation" in qs["Y"]["projections"][0]["field"]

    # Scatter: Columns -> X, Rows -> Y, Detail -> Category
    scatter = by_type["scatterChart"][0]["visual"]["query"]["queryState"]
    assert scatter["Category"]["projections"][0]["field"]["Column"]["Property"] == "Region"
    assert "Sum(Orders.Sales)" in scatter["X"]["projections"][0]["queryRef"]
    assert "Sum(Orders.Profit)" in scatter["Y"]["projections"][0]["queryRef"]

    text = by_type["textbox"][0]["visual"]["objects"]["general"][0]["properties"]["paragraphs"]
    assert text[0]["textRuns"][0]["value"] == "Sales overview"
    fill = by_type["shape"][0]["visual"]["objects"]["fill"][0]["properties"]["fillColor"]
    assert fill["solid"]["color"]["expr"]["Literal"]["Value"] == "'#34657f'"


def test_dashboard_status_lists_every_zone() -> None:
    files, _ = _convert()
    statuses = {s["worksheet"]: s for s in json.loads(files["conversion_status.json"])}
    dash = statuses["Dashboard: Overview"]
    rows = {(e["element"], e["status"]) for e in dash["elements"]}
    assert ("Parameter control", "Not converted") in rows
    assert ("Navigation buttons", "Partial") in rows
    assert ("Background block", "Full") not in rows  # header has text, so it is a Text row
    assert any(e["element"] == "Text" for e in dash["elements"])
    assert sum(e["element"] == "Sheet placement" for e in dash["elements"]) == 2

    trend = {(e["element"], e["tableau"]): e["status"] for e in statuses["Trend"]["elements"]}
    assert trend[("Visual type", "Line mark")] == "Full"
    assert trend[("Color", "Color: Segment")] == "Full"
    assert statuses["Trend"]["page"] == "Overview (dashboard)"


_ACTIONS = """
<actions>
<action caption='Filter 1' name='[Action1]'>
<activation auto-clear='true' type='on-select' />
<source dashboard='Overview' type='sheet' worksheet='Trend' />
<command command='tsc:tsl-filter'>
<param name='special-fields' value='all' />
<param name='target' value='Overview' />
<param name='exclude' value='Go Button' />
</command></action>
<action caption='Highlight 1' name='[Action2]'>
<activation auto-clear='true' type='on-select' />
<source dashboard='Overview' type='sheet' worksheet='Scatter' />
<command command='tsc:brush'>
<param name='field-captions' value='Region' />
<param name='target' value='Overview' />
</command></action>
<action caption='Open site' name='[Action3]'>
<source dashboard='Overview' type='sheet' worksheet='Trend' />
<command command='tsc:url'><param name='target' value='Overview' /></command></action>
</actions>
"""


def _convert_with_actions() -> tuple[dict[str, str], list]:
    twb = _TWB.replace("</workbook>", _ACTIONS + "</workbook>")
    path = Path(__file__).parent / "_tmp_dashboard_actions.twb"
    path.write_text(twb, encoding="utf-8")
    try:
        data, result = transpile_to_pbip(path)
    finally:
        path.unlink(missing_ok=True)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        files = {n: zf.read(n).decode("utf-8") for n in zf.namelist() if not n.endswith(".csv")}
    return files, result.worksheets


def test_dashboard_actions_become_visual_interactions() -> None:
    files, _ = _convert_with_actions()
    page = json.loads(next(v for k, v in files.items() if k.endswith("page.json") and "Overview" in v))
    names: dict[str, str] = {}
    for k, v in files.items():
        if k.endswith("visual.json") and f"/{page['name']}/" in k:
            body = json.loads(v)
            if body["visual"]["visualType"] == "lineChart":
                names["Trend"] = body["name"]
            elif body["visual"]["visualType"] == "scatterChart":
                names["Scatter"] = body["name"]
            elif body["visual"]["visualType"] == "pageNavigator":
                names["Nav"] = body["name"]
    interactions = {(i["source"], i["target"]): i["type"] for i in page["visualInteractions"]}
    # Trend filters Scatter; the excluded "Go Button" sheet is a page navigator (not a target)
    assert interactions[(names["Trend"], names["Scatter"])] == "DataFilter"
    # Scatter highlights Trend
    assert interactions[(names["Scatter"], names["Trend"])] == "HighlightFilter"


def test_excluded_sheet_gets_no_filter() -> None:
    from engine.pbip.dashboard import dashboard_interactions
    from engine.schema import DashboardAction, DashboardInfo

    dash = DashboardInfo(
        name="D",
        actions=[
            DashboardAction(
                caption="F", kind="filter", command="tsc:tsl-filter", source_dashboard="D",
                source_sheets=["A"], target="D", target_excluded="C, with comma,Z",
            )
        ],
    )
    visuals = {"A": "va", "B": "vb", "C, with comma": "vc"}
    interactions, rows = dashboard_interactions(dash, visuals)
    got = {(i["source"], i["target"]): i["type"] for i in interactions}
    assert got == {("va", "vb"): "DataFilter", ("va", "vc"): "NoFilter"}
    assert rows[0].status == "Full" and "specific targets" in rows[0].note


def test_non_filter_actions_are_reported_not_converted() -> None:
    files, _ = _convert_with_actions()
    statuses = {s["worksheet"]: s for s in json.loads(files["conversion_status.json"])}
    rows = {(e["element"], e["tableau"]): e["status"] for e in statuses["Dashboard: Overview"]["elements"]}
    assert rows[("Dashboard action", "Open site")] == "Not converted"
    assert rows[("Dashboard action", "Filter 1")] == "Full"
