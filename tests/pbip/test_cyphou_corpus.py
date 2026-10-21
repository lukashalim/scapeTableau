"""Run cyphou TableauToPowerBI sample workbooks through BIEscape PBIP export."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from engine.pbip import transpile_to_pbip
from engine.pbip.desktop_paths import looks_like_tableau_build_id

CYPHOU = Path("/tmp/cyphou-study/TableauToPowerBI")
SAMPLES = CYPHOU / "examples" / "tableau_samples"
REAL = CYPHOU / "examples" / "real_world"


def _workbooks() -> list[Path]:
    if not CYPHOU.is_dir():
        return []
    out = sorted(SAMPLES.glob("*.twb")) + sorted(REAL.glob("*.twb*"))
    return [p for p in out if p.is_file()]


@pytest.mark.skipif(not _workbooks(), reason="cyphou TableauToPowerBI clone missing")
@pytest.mark.parametrize("workbook", _workbooks(), ids=lambda p: p.name)
def test_cyphou_workbook_pbip(workbook: Path) -> None:
    zip_bytes, _result = transpile_to_pbip(workbook)
    assert zip_bytes[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        pbip = next(n for n in names if n.endswith(".pbip"))
        payload = json.loads(zf.read(pbip).decode("utf-8"))
    assert "$schema" not in payload
    assert set(payload) >= {"version", "artifacts", "settings"}
    project = pbip[: -len(".pbip")]
    assert not looks_like_tableau_build_id(project)
    assert f"{project}.Report/.platform" in names
    assert f"{project}.SemanticModel/.platform" in names


def test_heal_double_equals() -> None:
    from engine.pbip.dax_polish import heal_double_equals

    assert heal_double_equals("[Status] == \"Open\"") == '[Status] = "Open"'
    assert heal_double_equals("'T'[A] == 'T'[B]") == "'T'[A] = 'T'[B]"
    # Do not touch == inside strings
    assert '==' in heal_double_equals('"a == b"')
