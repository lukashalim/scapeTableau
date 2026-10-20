export type ConvertStatus =
  | { phase: "idle" }
  | { phase: "selected"; sourcePath: string }
  | { phase: "converting"; sourcePath: string; outputPath?: string }
  | {
      phase: "success";
      sourcePath: string;
      outputPath: string;
      sheets: string[];
      notes: string[];
    }
  | { phase: "error"; sourcePath?: string; error: string }
  | { phase: "analyzing"; sourcePath: string; dialect: string }
  | {
      phase: "sql_ready";
      sourcePath: string;
      dialect: string;
      sqlScript: string;
      workbook?: string | null;
      skipped: { caption: string; status: string; error?: string | null }[];
    };

export type SqlDialect = "ansi" | "snowflake" | "postgres" | "duckdb";
