"""Row data that lives next to a .twb (not inside a .twbx) is loaded into Data/."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from engine.pbip.data_csv import collect_data_csvs
from engine.schema import (
    ColumnField,
    ConnectionInfo,
    DatasourceInfo,
    RelationInfo,
    TranspileResult,
)


def _field(ds: str, caption: str) -> ColumnField:
    return ColumnField(name=caption, caption=caption, datasource=ds)


def test_sibling_csv_and_excel_fill_data_folder(tmp_path: Path) -> None:
    twb = tmp_path / "book.twb"
    twb.write_text("<workbook/>", encoding="utf-8")
    (tmp_path / "sales.csv").write_text("Region,Amount\nWest,10\nEast,4\n", encoding="utf-8")

    book = Workbook()
    sheet = book.active
    sheet.title = "HR data"
    sheet.append(["Name", "Attrition"])
    sheet.append(["Ada", 1])
    sheet.append(["Bea", 0])
    book.save(tmp_path / "HR Data.xlsx")

    sales = DatasourceInfo(
        name="sales",
        caption="sales",
        connections=[ConnectionInfo(class_name="textscan", filename="sales.csv")],
        relations=[RelationInfo(type="table", table="[sales#csv]")],
        fields=[_field("sales", "Region"), _field("sales", "Amount")],
    )
    hr = DatasourceInfo(
        name="hr",
        caption="HR data",
        connections=[
            ConnectionInfo(
                class_name="excel-direct",
                filename="C:/Users/someone/HR Data.xlsx",
            )
        ],
        relations=[RelationInfo(type="table", table="['HR data$']")],
        fields=[_field("hr", "Name"), _field("hr", "Attrition")],
    )
    result = TranspileResult(datasources=[sales, hr], workbook_name="book")
    files, notes, _headers = collect_data_csvs(twb, result, table_names={"sales": "sales", "hr": "HR data"})
    sales_csv = files["Data/sales.csv"].decode("utf-8-sig")
    hr_csv = files["Data/HR data.csv"].decode("utf-8-sig")
    assert "West" in sales_csv and "East" in sales_csv
    assert "Ada" in hr_csv and "Bea" in hr_csv
    assert any("sibling workbook" in note for note in notes)
