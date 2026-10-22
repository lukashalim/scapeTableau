"""Desktop path-budget helpers (adapted from cyphou/TableauToPowerBI, MIT).

Power BI Desktop enforces ~260 UTF-16 units for files and ~248 for directories.
Windows long-path prefixes do not lift that limit. See THIRD_PARTY_NOTICES.
"""

from __future__ import annotations

import hashlib
import re


class DesktopPathError(ValueError):
    """Path cannot fit within Power BI Desktop's ordinary-path budget."""


_TABLEAU_BUILD = re.compile(
    r"^\d+\.\d+(\.\d+)?\s*[\(_].*",
    re.IGNORECASE,
)


def path_units(path: str) -> int:
    """Windows/Desktop count UTF-16 code units, not Python characters."""
    return len(path.encode("utf-16-le", errors="surrogatepass")) // 2


def looks_like_tableau_build_id(name: str | None) -> bool:
    """True for Tableau ``source-build`` / repository ids used as workbook names."""
    if not name:
        return False
    return bool(_TABLEAU_BUILD.match(name.strip()))


def plan_project_name(
    preferred: str | None,
    *,
    fallback: str = "BIEscape",
    max_len: int = 64,
) -> str:
    """Sanitize a PBIP project name; reject Tableau build ids; hash-shorten if needed."""

    base = (preferred or "").strip() or fallback
    if looks_like_tableau_build_id(base):
        base = fallback
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" ._") or fallback
    if len(cleaned) > max_len:
        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:10]
        cleaned = f"{cleaned[: max_len - 11]}_{digest}"
    return cleaned


def predict_longest_paths(root: str, name: str) -> list[str]:
    """Worst-case artifact paths used to forecast the Desktop budget."""
    return [
        f"{root}/{name}/{name}.Report/definition/pages/"
        f"Drillthrough_12345678/visuals/12345678/visual.json",
        f"{root}/{name}/{name}.SemanticModel/definition/tables/12345678.tmdl",
        f"{root}/{name}/{name}.SemanticModel/definition/relationships.tmdl",
        f"{root}/{name}/{name}.pbip",
    ]


def fits_desktop_budget(root: str, name: str) -> bool:
    for path in predict_longest_paths(root, name):
        if path_units(path) >= 260 or path_units(path.rsplit("/", 1)[0]) >= 248:
            return False
    return True


def ensure_desktop_project_name(root: str, name: str) -> str:
    """Keep ``name`` when it fits under ``root``; otherwise hash-shorten."""
    if fits_desktop_budget(root, name):
        return name
    short = "P_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    if not fits_desktop_budget(root, short):
        raise DesktopPathError(
            f"Power BI Desktop path budget exceeded even for shortened name {short!r} "
            f"under {root!r} (files <260, directories <248 UTF-16 units)."
        )
    return short


def check_zip_paths(paths: list[str], *, root_hint: str = "C:/Users/User/Downloads") -> list[str]:
    """Return openability errors for zip-relative paths when unzipped under root_hint."""

    errors: list[str] = []
    for rel in paths:
        full = f"{root_hint.rstrip('/')}/{rel.replace(chr(92), '/')}"
        parent = full.rsplit("/", 1)[0]
        limit = 248 if rel.endswith("/") else 260
        # directories in zip listing rarely end with / — treat non-file extensions as files
        if path_units(full) >= 260:
            errors.append(
                f"path budget: '{rel}' would exceed Desktop's 260-char file limit "
                f"when unzipped under a typical Downloads folder"
            )
        elif path_units(parent) >= 248:
            errors.append(
                f"path budget: parent of '{rel}' would exceed Desktop's 248-char directory limit"
            )
        _ = limit
    return errors
