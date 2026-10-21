"""DataFolder CSV layout + data-bound PBIR visuals per page."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from engine.pbip import transpile_to_pbip
from engine.pbip.pbir import visual_is_data_bound

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
SUPERSTORE = Path("/cursor/stores/self/internal/US_Superstore_10.0.twbx")
if not SUPERSTORE.is_file():
    SUPERSTORE = SAMPLES / "US_Superstore_10.0.twbx"


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return set(zf.namelist())


def _read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return zf.read(name).decode("utf-8")


def test_datafolder_expression_and_csv_files() -> None:
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    names = _zip_names(zip_bytes)
    expr_path = next(n for n in names if n.endswith("expressions.tmdl"))
    expr = _read(zip_bytes, expr_path)
    assert 'expression DataFolder = "Data"' in expr
    assert "IsParameterQueryRequired=true" in expr
    data_csvs = [n for n in names if n.startswith("Data/") and n.endswith(".csv")]
    assert data_csvs, "expected Data/*.csv next to the .pbip in the zip"
    # File-based table M must concatenate DataFolder
    table_blob = "\n".join(
        _read(zip_bytes, n)
        for n in names
        if "/definition/tables/" in n and n.endswith(".tmdl") and "__Measures" not in n
    )
    assert "DataFolder &" in table_blob
    assert "File.Contents(DataFolder" in table_blob


def test_each_page_has_data_bound_visual() -> None:
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        pages = [n for n in zf.namelist() if n.endswith("/page.json")]
        assert pages
        for page in pages:
            page_dir = page.rsplit("/", 1)[0]
            visuals = [
                n
                for n in zf.namelist()
                if n.startswith(page_dir + "/visuals/") and n.endswith("/visual.json")
            ]
            assert visuals, f"{page_dir} has no visual.json"
            bound = 0
            for vn in visuals:
                visual = json.loads(zf.read(vn).decode("utf-8"))
                vtype = ((visual.get("visual") or {}).get("visualType") or "").lower()
                assert vtype not in {"textbox", "shape", "actionbutton"}
                if visual_is_data_bound(visual):
                    bound += 1
                    # Measure bindings must target __Measures home table
                    qs = ((visual.get("visual") or {}).get("query") or {}).get("queryState") or {}
                    for role in qs.values():
                        if not isinstance(role, dict):
                            continue
                        for proj in role.get("projections") or []:
                            field = (proj.get("field") or {})
                            meas = field.get("Measure")
                            if meas:
                                entity = (
                                    ((meas.get("Expression") or {}).get("SourceRef") or {}).get(
                                        "Entity"
                                    )
                                )
                                assert entity == "__Measures", (vn, entity)
            assert bound >= 1, f"{page_dir} has no data-bound visual"


def test_superstore_pages_data_bound_and_data_csvs() -> None:
    if not SUPERSTORE.is_file():
        return
    zip_bytes, _ = transpile_to_pbip(SUPERSTORE)
    names = _zip_names(zip_bytes)
    assert any(n.startswith("Data/") and n.endswith(".csv") for n in names)
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        pages = [n for n in zf.namelist() if n.endswith("/page.json")]
        assert len(pages) >= 1
        for page in pages:
            page_dir = page.rsplit("/", 1)[0]
            visuals = [
                n
                for n in zf.namelist()
                if n.startswith(page_dir + "/visuals/") and n.endswith("/visual.json")
            ]
            assert any(
                visual_is_data_bound(json.loads(zf.read(vn).decode("utf-8"))) for vn in visuals
            )
