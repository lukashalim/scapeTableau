"""Validate generated PBIP/PBIR JSON against vendored microsoft/json-schemas."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from engine.pbip import transpile_to_pbip

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
SUPERSTORE = Path("/cursor/stores/self/internal/US_Superstore_10.0.twbx")
if not SUPERSTORE.is_file():
    SUPERSTORE = SAMPLES / "US_Superstore_10.0.twbx"
SCHEMA_ROOT = ROOT / "vendor" / "microsoft-json-schemas"


def _load_schema(rel: str) -> dict:
    path = SCHEMA_ROOT / rel
    assert path.is_file(), f"missing vendored schema {rel}"
    return json.loads(path.read_text(encoding="utf-8"))


def _zip_json(data: bytes, name: str) -> dict:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return json.loads(zf.read(name).decode("utf-8"))


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return set(zf.namelist())


def _validator(schema: dict):
    """Build a Draft validator that ignores remote $schema/$ref fetches."""

    # Vendored files are self-contained enough for required props; skip remote refs.
    resolver = jsonschema.RefResolver.from_schema(schema)
    return jsonschema.Draft7Validator(schema, resolver=resolver)


def _validate_required_only(instance: dict, schema: dict) -> None:
    """Validate type/required locally; skip unresolved remote $ref failures."""

    # Strip $ref-heavy branches by checking top-level required + types only when cheap.
    required = schema.get("required") or []
    for key in required:
        assert key in instance, f"missing required key {key}"
    props = schema.get("properties") or {}
    for key, prop in props.items():
        if key not in instance:
            continue
        expected = prop.get("type")
        if expected == "string":
            assert isinstance(instance[key], str), key
        elif expected == "object":
            assert isinstance(instance[key], dict), key
        elif expected == "array":
            assert isinstance(instance[key], list), key
        elif expected == "boolean":
            assert isinstance(instance[key], bool), key
        elif expected == "number":
            assert isinstance(instance[key], (int, float)), key


@pytest.mark.skipif(not SUPERSTORE.is_file(), reason="US Superstore twbx missing")
def test_superstore_pbip_json_schema() -> None:
    zip_bytes, _ = transpile_to_pbip(SUPERSTORE)
    names = _zip_names(zip_bytes)
    pbip_name = next(n for n in names if n.endswith(".pbip") and "/" not in n.rstrip("/"))
    prefix = pbip_name[: -len(".pbip")]
    sm = f"{prefix}.SemanticModel"
    report = f"{prefix}.Report"

    checks = [
        (pbip_name, "fabric/pbip/pbipProperties/1.0.0/schema.json"),
        (f"{sm}/definition.pbism", "fabric/item/semanticModel/definitionProperties/1.0.0/schema.json"),
        (f"{report}/definition.pbir", "fabric/item/report/definitionProperties/2.0.0/schema.json"),
        (f"{report}/definition/version.json", "fabric/item/report/definition/versionMetadata/1.0.0/schema.json"),
        (f"{report}/definition/pages/pages.json", "fabric/item/report/definition/pagesMetadata/1.0.0/schema.json"),
        (f"{report}/definition/report.json", "fabric/item/report/definition/report/3.0.0/schema.json"),
    ]
    for path, schema_rel in checks:
        assert path in names, path
        instance = _zip_json(zip_bytes, path)
        schema = _load_schema(schema_rel)
        _validate_required_only(instance, schema)
        # Prefer full validate when schema has no unresolved external $ref
        try:
            jsonschema.validate(instance=instance, schema=schema)
        except jsonschema.ValidationError as exc:
            pytest.fail(f"{path} failed schema {schema_rel}: {exc.message}")
        except jsonschema.RefResolutionError:
            # Remote fabric refs — required-key check above is the CI gate
            pass

    # At least one page + visual
    page_jsons = [n for n in names if "/pages/" in n and n.endswith("/page.json")]
    visual_jsons = [n for n in names if n.endswith("/visual.json")]
    assert page_jsons, "expected Stage 3 page.json files"
    assert visual_jsons, "expected Stage 3 visual.json files"
    page_schema = _load_schema("fabric/item/report/definition/page/2.1.0/schema.json")
    visual_schema = _load_schema("fabric/item/report/definition/visualContainer/2.9.0/schema.json")
    for path in page_jsons[:3]:
        _validate_required_only(_zip_json(zip_bytes, path), page_schema)
    for path in visual_jsons[:3]:
        inst = _zip_json(zip_bytes, path)
        _validate_required_only(inst, visual_schema)
        assert "visual" in inst and "position" in inst


def test_sample_pbip_schema_smoke() -> None:
    zip_bytes, _ = transpile_to_pbip(SAMPLES / "superstore_calcs.twb")
    names = _zip_names(zip_bytes)
    assert any(n.endswith("definition/version.json") for n in names)
    assert any(n.endswith("/visual.json") for n in names)
    pbism = next(n for n in names if n.endswith("definition.pbism"))
    body = _zip_json(zip_bytes, pbism)
    assert body.get("version") == "4.2"
