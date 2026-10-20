export type ConvertStatus =
  | { phase: "idle" }
  | { phase: "converting"; sourcePath: string; outputPath?: string }
  | {
      phase: "success";
      sourcePath: string;
      outputPath: string;
      sheets: string[];
      notes: string[];
    }
  | { phase: "error"; sourcePath?: string; error: string };
