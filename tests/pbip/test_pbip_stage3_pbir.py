"""Stage 3 PBIR pages/visuals + Franssens template markers."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from engine.pbip import transpile_to_pbip
from engine.pbip.dax_polish import hoist_compact_measure_filters

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _names(data: bytes) -> set[str]:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return set(zf.namelist())


def _read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return zf.read(name).decode("utf-8")


def test_stage3_pbir_definition_tree() -> None:
    zip_bytes, result = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    names = _names(zip_bytes)
    assert any(n.endswith("definition/version.json") for n in names)
    assert any(n.endswith("definition/pages/pages.json") for n in names)
    assert any(n.endswith("/page.json") for n in names)
    assert any(n.endswith("/visual.json") for n in names)
    # Legacy flat report.json removed in favor of definition/report.json
    assert not any(n.endswith(".Report/report.json") for n in names)
    db = next(n for n in names if n.endswith("database.tmdl"))
    assert "1702" in _read(zip_bytes, db)
    assert result.worksheets  # worksheets surfaced for Stage 3


def test_hoist_compact_measure_filters() -> None:
    raw = "CALCULATE(SUM('T'[Sales]), 'T'[Year] = [Year Parameter Value])"
    out = hoist_compact_measure_filters(raw)
    assert "VAR _f1" in out
    assert "RETURN CALCULATE" in out
    assert "[Year Parameter Value]" in out
    # Literals unchanged path
    lit = "CALCULATE(SUM(T[Sales]), T[Flag] = 1)"
    assert hoist_compact_measure_filters(lit) == lit or "VAR" not in hoist_compact_measure_filters(lit)
