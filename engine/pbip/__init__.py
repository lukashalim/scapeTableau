"""Power BI Project (.pbip) Stage 1 semantic model + Stage 2 DAX packaging."""

from __future__ import annotations

from pathlib import Path

from engine import transpile
from engine.pbip.package import build_pbip_files, build_pbip_zip, write_pbip_dir
from engine.schema import TranspileResult

__all__ = [
    "build_pbip_zip",
    "build_pbip_files",
    "write_pbip_dir",
    "transpile_to_pbip",
]


def _source_stem(source) -> str | None:
    if isinstance(source, Path):
        return source.stem
    if isinstance(source, str) and (source.endswith(".twb") or source.endswith(".twbx")):
        return Path(source).stem
    return None


def transpile_to_pbip(source, *, dialect: str = "ansi") -> tuple[bytes, TranspileResult]:
    """Transpile a workbook and return (zip_bytes, result)."""

    result = transpile(source, dialect=dialect, target="sql")
    return build_pbip_zip(result, source_stem=_source_stem(source)), result
