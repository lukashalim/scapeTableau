"""Optional tableaudocumentapi cross-check — skip if install/XML fights us."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.parser import parse_workbook

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_document_api_field_names_crosscheck() -> None:
    try:
        from tableaudocumentapi import Workbook
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tableaudocumentapi not importable: {exc}")

    path = SAMPLES / "superstore_calcs.twb"
    try:
        doc = Workbook(str(path))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tableaudocumentapi cannot open sample (expected): {exc}")

    ours = parse_workbook(path)
    our_names: set[str] = set()
    for ds in ours.datasources:
        for f in ds.fields:
            if f.caption:
                our_names.add(f.caption)
            if f.name:
                our_names.add(f.name.strip("[]"))

    doc_names: set[str] = set()
    for ds in doc.datasources:
        for name in ds.fields:
            doc_names.add(name)
            field = ds.fields[name]
            cap = getattr(field, "caption", None)
            if cap:
                doc_names.add(cap)

    # Soft overlap — document API is a cross-check, not source of truth
    overlap = our_names & doc_names
    assert overlap, "expected some shared field names between parsers"
