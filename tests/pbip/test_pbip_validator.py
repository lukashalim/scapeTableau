"""Post-build PBIP validator coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.pbip import build_pbip_files, transpile_to_pbip
from engine.pbip.validate import PbipValidationError, validate_pbip_files

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def test_validator_passes_sample() -> None:
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    assert zip_bytes[:2] == b"PK"


def test_validator_catches_missing_theme_file() -> None:
    from engine import transpile

    result = transpile(SAMPLES / "superstore_calcs.twb")
    files, _ = build_pbip_files(result)
    # Remove theme while leaving resourcePackages reference
    theme_keys = [k for k in files if "BaseThemes/CY24SU10.json" in k]
    assert theme_keys
    for k in theme_keys:
        del files[k]
    with pytest.raises(PbipValidationError) as exc:
        validate_pbip_files(files, fail=True)
    assert any("missing file" in e for e in exc.value.errors)


def test_no_extract_or_bare_sum_in_superstore() -> None:
    from engine import transpile

    result = transpile(SAMPLES / "US_Superstore_10.0.twbx")
    blob = "\n".join(f.dax or "" for f in result.calculated_fields)
    assert "Extract[" not in blob
    assert "SUM('Sales')" not in blob
    assert "SUM('Profit')" not in blob
    assert "'Sample - Superstore'" in blob or "Sample - Superstore" in blob
