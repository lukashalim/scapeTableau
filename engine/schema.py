"""Pydantic v2 models for Tableau workbook parse and transpile results."""

from __future__ import annotations

from enum import Enum
from typing import Any

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


class ConnectionInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    class_name: str | None = Field(default=None, alias="class")
    dbname: str | None = None
    server: str | None = None
    filename: str | None = None


class RelationInfo(BaseModel):
    name: str | None = None
    type: str | None = None
    table: str | None = None
    custom_sql: str | None = None


class DatasourceInfo(BaseModel):
    name: str
    caption: str | None = None
    connections: list[ConnectionInfo] = Field(default_factory=list)
    relations: list[RelationInfo] = Field(default_factory=list)


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


class TranspileResult(BaseModel):
    calculated_fields: list[CalculatedField] = Field(default_factory=list)
    datasources: list[DatasourceInfo] = Field(default_factory=list)
    sql_script: str = ""
    markdown_audit: str = ""
    workbook_name: str | None = None
    dialect: str = "ansi"

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
