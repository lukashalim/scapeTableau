"""Post-build PBIP validator coverage."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from engine import transpile
from engine.pbip import build_pbip_files, transpile_to_pbip
from engine.pbip.validate import PbipValidationError, validate_pbip_files

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def test_validator_passes_sample() -> None:
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    assert zip_bytes[:2] == b"PK"


def test_pbip_project_file_has_no_schema_key() -> None:
    """Desktop silently refuses .pbip files that include $schema — match its own output."""
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        pbip_name = next(n for n in zf.namelist() if n.endswith(".pbip"))
        payload = json.loads(zf.read(pbip_name).decode("utf-8"))
    assert "$schema" not in payload
    assert "version" in payload
    assert "artifacts" in payload
    assert "settings" in payload
    # Validator must also reject a .pbip that still carries $schema
    files, _ = build_pbip_files(transpile(SAMPLES / "superstore_calcs.twb"))
    pbip_key = next(k for k in files if k.endswith(".pbip"))
    broken = json.loads(files[pbip_key])
    broken["$schema"] = (
        "https://developer.microsoft.com/json-schemas/fabric/pbip/"
        "pbipProperties/1.0.0/schema.json"
    )
    files[pbip_key] = json.dumps(broken, indent=2) + "\n"
    with pytest.raises(PbipValidationError) as exc:
        validate_pbip_files(files, fail=True)
    assert any("$schema" in e for e in exc.value.errors)


def test_validator_catches_missing_theme_file() -> None:
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
    result = transpile(SAMPLES / "US_Superstore_10.0.twbx")
    blob = "\n".join(f.dax or "" for f in result.calculated_fields)
    assert "Extract[" not in blob
    assert "SUM('Sales')" not in blob
    assert "SUM('Profit')" not in blob
    assert "'Sample - Superstore'" in blob or "Sample - Superstore" in blob
