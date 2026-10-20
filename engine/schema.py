"""Pydantic v2 models for Tableau workbook parse and transpile results."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkbookParseError(Exception):
    """Invalid zip/XML or unsupported workbook payload."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ParseStatus(str, Enum):
    ok = "ok"
    unparseable = "unparseable"
    error = "error"
    skipped = "skipped"


ArchiveKind = Literal["twb", "csv", "xlsx", "hyper", "other"]


class ConnectionInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    class_name: str | None = Field(default=None, alias="class")
    dbname: str | None = None
    server: str | None = None
    filename: str | None = None
    username: str | None = None
    password: str | None = None
    warehouse: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")


class RelationInfo(BaseModel):
    name: str | None = None
    type: str | None = None
    table: str | None = None
    custom_sql: str | None = None


class ColumnField(BaseModel):
    name: str
    caption: str
    datatype: str | None = None
    role: str | None = None
    hidden: bool = False
    calculated: bool = False
    formula: str | None = None
    datasource: str
    datasource_caption: str | None = None


class ParameterInfo(BaseModel):
    name: str
    caption: str
    datatype: str | None = None
    value: str | None = None
    formula: str | None = None
    datasource: str = "Parameters"


class DashboardZone(BaseModel):
    type: str | None = None
    name: str | None = None
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    children: list[DashboardZone] = Field(default_factory=list)


class DashboardInfo(BaseModel):
    name: str
    width: int | None = None
    height: int | None = None
    zones: list[DashboardZone] = Field(default_factory=list)
    svg: str | None = None
    html: str | None = None


class WorksheetInfo(BaseModel):
    name: str
    datasource: str | None = None
    rows: list[str] = Field(default_factory=list)
    cols: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    used_fields: list[str] = Field(default_factory=list)


class ArchiveMember(BaseModel):
    path: str
    size: int
    kind: ArchiveKind


class CustomSqlEntry(BaseModel):
    name: str | None = None
    datasource: str | None = None
    sql: str


class ExtractInventory(BaseModel):
    archive_members: list[ArchiveMember] = Field(default_factory=list)
    tabular_files: list[ArchiveMember] = Field(default_factory=list)
    hyper_files: list[ArchiveMember] = Field(default_factory=list)
    custom_sql: list[CustomSqlEntry] = Field(default_factory=list)
    converted_sheets: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class InspectCounts(BaseModel):
    datasources: int = 0
    fields: int = 0
    hidden_fields: int = 0
    calculated_fields: int = 0
    parameters: int = 0
    dashboards: int = 0
    archive_members: int = 0
    custom_sql: int = 0
    hyper_files: int = 0


class InspectSnapshot(BaseModel):
    workbook_name: str | None = None
    counts: InspectCounts = Field(default_factory=InspectCounts)
    connections: list[ConnectionInfo] = Field(default_factory=list)
    fields: list[ColumnField] = Field(default_factory=list)
    parameters: list[ParameterInfo] = Field(default_factory=list)
    dashboards: list[DashboardInfo] = Field(default_factory=list)
    archive_members: list[ArchiveMember] = Field(default_factory=list)


class DatasourceInfo(BaseModel):
    name: str
    caption: str | None = None
    connections: list[ConnectionInfo] = Field(default_factory=list)
    relations: list[RelationInfo] = Field(default_factory=list)
    fields: list[ColumnField] = Field(default_factory=list)


class CalculatedField(BaseModel):
    name: str
    caption: str
    datatype: str | None = None
    role: str | None = None
    formula: str
    sql: str
    status: ParseStatus
    datasource: str
    error: str | None = None
    dax: str = ""
    dax_status: ParseStatus | None = None
    dax_kind: str | None = None
    sql_kind: str | None = None  # "row" | "aggregate" when status is ok

    @property
    def raw_formula(self) -> str:
        """Tableau formula text; JSON continues to serialize as ``formula``."""
        return self.formula

    @property
    def sql_expression(self) -> str:
        """Transpiled SQL; JSON continues to serialize as ``sql``."""
        return self.sql


class TranspileResult(BaseModel):
    calculated_fields: list[CalculatedField] = Field(default_factory=list)
    datasources: list[DatasourceInfo] = Field(default_factory=list)
    sql_script: str = ""
    markdown_audit: str = ""
    workbook_name: str | None = None
    dialect: str = "ansi"
    inspect: InspectSnapshot | None = None
    extract_inventory: ExtractInventory | None = None
    dashboards: list[DashboardInfo] = Field(default_factory=list)
    dax_script: str = ""
    target: str = "sql"

    def model_dump_json_result(self) -> str:
        return self.model_dump_json(indent=2)


class RawCalculatedField(BaseModel):
    """Parser-internal calculated column before SQL generation."""

    name: str
    caption: str
    datatype: str | None = None
    role: str | None = None
    formula: str
    datasource_name: str
    datasource_caption: str | None = None


class ParsedWorkbook(BaseModel):
    workbook_name: str | None = None
    datasources: list[DatasourceInfo] = Field(default_factory=list)
    calculated_fields: list[RawCalculatedField] = Field(default_factory=list)
    name_maps: dict[str, dict[str, str]] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)
    archive_members: list[ArchiveMember] = Field(default_factory=list)
    parameters: list[ParameterInfo] = Field(default_factory=list)
    dashboards: list[DashboardInfo] = Field(default_factory=list)
    fields: list[ColumnField] = Field(default_factory=list)
    worksheets: list[WorksheetInfo] = Field(default_factory=list)
