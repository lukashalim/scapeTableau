export interface ConnectionInfo {
  class_name?: string | null;
  dbname?: string | null;
  server?: string | null;
  filename?: string | null;
}

export interface RelationInfo {
  name?: string | null;
  type?: string | null;
  table?: string | null;
  custom_sql?: string | null;
}

export interface DatasourceInfo {
  name: string;
  caption?: string | null;
  connections: ConnectionInfo[];
  relations: RelationInfo[];
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
}

export interface TranspileResult {
  calculated_fields: CalculatedField[];
  datasources: DatasourceInfo[];
  sql_script: string;
  markdown_audit: string;
  workbook_name?: string | null;
  dialect?: string;
}

export interface TranspileErrorBody {
  error: string;
}
