export interface ConnectionInfo {
  class_name?: string | null;
  dbname?: string | null;
  server?: string | null;
  filename?: string | null;
  username?: string | null;
  password?: string | null;
  warehouse?: string | null;
  schema_name?: string | null;
}

export interface RelationInfo {
  name?: string | null;
  type?: string | null;
  table?: string | null;
  custom_sql?: string | null;
}

export interface ColumnField {
  name: string;
  caption: string;
  datatype?: string | null;
  role?: string | null;
  hidden: boolean;
  calculated: boolean;
  formula?: string | null;
  datasource: string;
  datasource_caption?: string | null;
}

export interface ParameterInfo {
  name: string;
  caption: string;
  datatype?: string | null;
  value?: string | null;
  formula?: string | null;
  datasource?: string;
}

export interface DashboardZone {
  type?: string | null;
  name?: string | null;
  x?: number | null;
  y?: number | null;
  w?: number | null;
  h?: number | null;
  children: DashboardZone[];
}

export interface DashboardInfo {
  name: string;
  width?: number | null;
  height?: number | null;
  zones: DashboardZone[];
  svg?: string | null;
  html?: string | null;
}

export interface ArchiveMember {
  path: string;
  size: number;
  kind: "twb" | "csv" | "xlsx" | "hyper" | "other";
}

export interface CustomSqlEntry {
  name?: string | null;
  datasource?: string | null;
  sql: string;
}

export interface ExtractInventory {
  archive_members: ArchiveMember[];
  tabular_files: ArchiveMember[];
  hyper_files: ArchiveMember[];
  custom_sql: CustomSqlEntry[];
  converted_sheets: string[];
  notes: string[];
}

export interface InspectCounts {
  datasources: number;
  fields: number;
  hidden_fields: number;
  calculated_fields: number;
  parameters: number;
  dashboards: number;
  archive_members: number;
  custom_sql: number;
  hyper_files: number;
}

export interface InspectSnapshot {
  workbook_name?: string | null;
  counts: InspectCounts;
  connections: ConnectionInfo[];
  fields: ColumnField[];
  parameters: ParameterInfo[];
  dashboards: DashboardInfo[];
  archive_members: ArchiveMember[];
}

export interface DatasourceInfo {
  name: string;
  caption?: string | null;
  connections: ConnectionInfo[];
  relations: RelationInfo[];
  fields?: ColumnField[];
}

export interface CalculatedField {
  name: string;
  caption: string;
  datatype?: string | null;
  role?: string | null;
  formula: string;
  sql: string;
  status: "ok" | "unparseable" | "error" | "skipped";
  datasource: string;
  error?: string | null;
  dax?: string | null;
  dax_status?: "ok" | "unparseable" | "error" | "skipped" | null;
}

export interface TranspileResult {
  calculated_fields: CalculatedField[];
  datasources: DatasourceInfo[];
  sql_script: string;
  markdown_audit: string;
  dax_script?: string | null;
  workbook_name?: string | null;
  dialect?: string;
  target?: string;
  inspect?: InspectSnapshot | null;
  extract_inventory?: ExtractInventory | null;
  dashboards?: DashboardInfo[];
}

export interface TranspileErrorBody {
  error: string;
}
