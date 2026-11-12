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
    port: str | None = None
    sslmode: str | None = None
    warehouse: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")


class RelationInfo(BaseModel):
    name: str | None = None
    type: str | None = None
    table: str | None = None
    custom_sql: str | None = None


class JoinInfo(BaseModel):
    """Tableau join clause mapped to a Power BI many-to-one relationship."""

    left_datasource: str
    right_datasource: str
    left_column: str
    right_column: str
    join_type: str | None = None  # inner | left | right | full


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
    domain_type: str | None = None  # list | range | any
    allowed_values: list[str] = Field(default_factory=list)
    range_min: str | None = None
    range_max: str | None = None


class DashboardZone(BaseModel):
    type: str | None = None
    name: str | None = None
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    # Parameter control zones point at a parameter, e.g. "[Parameters].[Parameter 3]"
    param: str | None = None
    # Text zones: plain text (runs joined) and its first run's styling
    text: str | None = None
    font_color: str | None = None
    font_size: str | None = None
    font_name: str | None = None
    # zone-style background-color, e.g. "#34657f"
    fill: str | None = None
    # Dashboard ``show-title``. False hides the Power BI visual title.
    show_title: bool | None = None
    children: list[DashboardZone] = Field(default_factory=list)


class DashboardAction(BaseModel):
    """A dashboard action (``<action>``): filter / highlight / url / go-to-sheet / parameter."""

    name: str | None = None
    caption: str | None = None
    # "filter" (tsc:tsl-filter), "highlight" (tsc:brush) or "other"
    kind: str = "other"
    command: str | None = None
    source_dashboard: str | None = None
    # Worksheets that trigger the action; empty means every sheet on the source dashboard
    source_sheets: list[str] = Field(default_factory=list)
    # Sheets removed from the trigger set (``exclude-sheet`` on <source>)
    source_excluded: list[str] = Field(default_factory=list)
    # Dashboard (or sheet) the action applies to
    target: str | None = None
    # Raw comma separated list of sheets that do NOT react to the action
    target_excluded: str | None = None
    # False when the author restricted the action to selected fields
    all_fields: bool = True


class DashboardInfo(BaseModel):
    name: str
    width: int | None = None
    height: int | None = None
    zones: list[DashboardZone] = Field(default_factory=list)
    actions: list[DashboardAction] = Field(default_factory=list)
    svg: str | None = None
    html: str | None = None


class WorksheetInfo(BaseModel):
    name: str
    datasource: str | None = None
    rows: list[str] = Field(default_factory=list)
    cols: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    used_fields: list[str] = Field(default_factory=list)
    mark_class: str | None = None
    # Shelf field -> Tableau derivation for plain aggregated pills, e.g. {"Sales": "sum"}
    aggregations: dict[str, str] = Field(default_factory=dict)
    # Fields on the Detail ("lod") mark encoding, e.g. ["State"] on a filled map
    detail_fields: list[str] = Field(default_factory=list)
    # Categorical member filters: field -> kept string members, e.g. {"Region": ["Central"]}
    filters: dict[str, list[str]] = Field(default_factory=dict)
    # Mark encodings by kind, e.g. {"color": ["Profit"], "tooltip": ["Sales"], "text": [...]}
    encodings: dict[str, list[str]] = Field(default_factory=dict)
    custom_tooltip: bool = False
    # Sorts as "Field" strings from computed-sort / manual-sort elements
    sorts: list[str] = Field(default_factory=list)
    # Filters we cannot mirror (quantitative, relative date, exclude, context ...): "Field (class)"
    unmapped_filters: list[str] = Field(default_factory=list)
    # Worksheet-local calcs that are constants such as MIN(1); they only size marks
    constant_fields: list[str] = Field(default_factory=list)
    # Mark labels switched on for the sheet (Tableau "Show mark labels")
    show_labels: bool = False
    # Aggregation of plain aggregated pills used on encodings, e.g. {"Sales": "sum"}
    encoding_aggregations: dict[str, str] = Field(default_factory=dict)
    # Sorts with direction: [{"field": "Region", "direction": "DESC", "using": "Sales"}]
    sort_specs: list[dict[str, str]] = Field(default_factory=list)
    # Categorical filters with no member list: candidates for a Power BI slicer
    slicer_filters: list[str] = Field(default_factory=list)
    # Formatting carried over: title text/colour, background and mark colours
    title: str | None = None
    title_color: str | None = None
    background_color: str | None = None
    mark_color: str | None = None
    # field -> Tableau number format string (e.g. {"Sales": 'c"$"#,##0'})
    number_formats: dict[str, str] = Field(default_factory=dict)
    # Geometry encoding on a generated lat/long sheet: Tableau is drawing polygons (a filled map).
    has_geometry: bool = False
    # Fixed map viewport in degrees, from a Tableau axis with range-type="fixed".
    map_west: float | None = None
    map_south: float | None = None
    map_east: float | None = None
    map_north: float | None = None
    # Country and state names drawn on the base map. Tableau can switch those layers off.
    map_place_labels: bool = True
    # Interpolated colour palette on the Colour shelf, e.g. tableau-map-blue-green.
    color_palette: str | None = None
    # ``display-field-labels`` on rows. Hides the "Industry" axis title.
    show_field_labels: bool = True
    # The value axis ``display`` format. Tableau turns the bar axis off and keeps the labels.
    hide_value_axis: bool = False


class MeasureNamesUsage(BaseModel):
    """Worksheet use of Tableau Measure Names / Measure Values layout tokens."""

    worksheet: str
    datasource: str | None = None
    measures: list[str] = Field(default_factory=list)
    shelves: list[str] = Field(default_factory=list)
    strategy: Literal["a", "b", "c"] = "a"
    has_measure_names: bool = False
    has_measure_values: bool = False


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
    # Tableau ``<extract enabled="true">``. The workbook is reading the extract,
    # so the original database connection is not the data source to keep.
    extract_enabled: bool = False


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
    dax_notes: list[str] = Field(default_factory=list)  # missing-column TODOs, etc.

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
    measure_names: list[MeasureNamesUsage] = Field(default_factory=list)
    conversion_summary: str = ""
    joins: list[JoinInfo] = Field(default_factory=list)
    worksheets: list[WorksheetInfo] = Field(default_factory=list)
    # Tableau internal column name -> caption, per datasource name
    name_maps: dict[str, dict[str, str]] = Field(default_factory=dict)

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
    # Worksheet <table-calc> pills (percent of total, etc.) — listed; Stage 2 emits DAX.
    is_table_calc: bool = False
    worksheet: str | None = None
    table_calc_type: str | None = None
    # CNT of a Tableau object-model table (datatype='table'), not a real column.
    is_row_count: bool = False


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
    measure_names: list[MeasureNamesUsage] = Field(default_factory=list)
    joins: list[JoinInfo] = Field(default_factory=list)
