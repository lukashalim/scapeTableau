"""CONVERSION_REPORT.md builders and measure/column disposition logging."""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.schema import CalculatedField, ParseStatus


@dataclass
class ConversionEntry:
    caption: str
    datasource: str
    original_formula: str
    dax: str
    kind: str  # measure | calculated_column | skipped
    disposition: str  # converted | approximated | skipped
    reason: str


@dataclass
class ConversionReport:
    entries: list[ConversionEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    hyper_todos: list[str] = field(default_factory=list)
    parameter_notes: list[str] = field(default_factory=list)
    dax_todos: list[str] = field(default_factory=list)

    def add(self, entry: ConversionEntry) -> None:
        self.entries.append(entry)

    def to_markdown(self) -> str:
        lines = [
            "# EscapeTableau → PBIP conversion report",
            "",
            "Generated for Power BI Desktop project (`.pbip`) Stage 1 semantic model + Stage 2 DAX measures.",
            "",
            "**Manual check (Lukas):** unzip the download, open `<Name>.pbip` in Power BI Desktop, "
            "click Refresh now / Apply changes (small tables are embedded — no Postgres login), "
            "and compare key measures (Profit Ratio, Profit per Customer, "
            "Achieved Quota) against Tableau.",
            "",
            "## Summary",
            "",
        ]
        counts = {"converted": 0, "approximated": 0, "skipped": 0}
        for e in self.entries:
            counts[e.disposition] = counts.get(e.disposition, 0) + 1
        lines.append(
            f"- converted: **{counts.get('converted', 0)}** · "
            f"approximated: **{counts.get('approximated', 0)}** · "
            f"skipped: **{counts.get('skipped', 0)}**"
        )
        lines.append("")
        if self.parameter_notes:
            lines.append("## Parameters")
            lines.append("")
            for note in self.parameter_notes:
                lines.append(f"- {note}")
            lines.append("")
        if self.hyper_todos:
            lines.append("## Hyper extracts")
            lines.append("")
            for note in self.hyper_todos:
                lines.append(f"- {note}")
            lines.append("")
        if self.dax_todos:
            lines.append("## DAX TODOs")
            lines.append("")
            for note in self.dax_todos:
                lines.append(f"- TODO: {note}")
            lines.append("")
        if self.notes:
            lines.append("## Notes")
            lines.append("")
            for note in self.notes:
                lines.append(f"- {note}")
            lines.append("")
        lines.extend(
            [
                "## Calculated fields",
                "",
                "| Caption | Kind | Disposition | Tableau formula | DAX | Reason |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for e in self.entries:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(e.caption),
                        _md(e.kind),
                        _md(e.disposition),
                        _md(e.original_formula),
                        _md(e.dax),
                        _md(e.reason),
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.append(
            "## Filter semantics\n\n"
            "Tableau FIXED ignores dimension filters (except context filters). "
            "Power BI `CALCULATE` + `ALLEXCEPT` / `ALL` does **not** automatically ignore slicers from "
            "related tables — verify slicer interactions after open.\n"
        )
        return "\n".join(lines)


def _md(value: str | None) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def disposition_for_field(field: CalculatedField) -> tuple[str, str]:
    """Return (disposition, reason) for a transpile result field."""

    if field.status == ParseStatus.skipped or (field.dax_status == ParseStatus.skipped):
        return "skipped", field.error or "Skipped by translator"
    if field.status != ParseStatus.ok or (field.dax_status not in (None, ParseStatus.ok)):
        return "skipped", field.error or f"status={field.status.value}"
    dax = field.dax or ""
    if "ALLSELECTED" in dax or "RANKX" in dax:
        return "approximated", "Table calc / window approximation"
    if "ALLEXCEPT" in dax or "ALL(" in dax:
        return "approximated", "FIXED LOD → CALCULATE filters (slicer semantics differ)"
    if field.sql_kind == "aggregate" or field.dax_kind == "measure":
        return "converted", "Aggregate measure"
    return "converted", "Row-level calculated column"
