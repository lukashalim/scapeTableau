"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode, type RefObject } from "react";

import type { TranspileResult } from "@/lib/transpile-types";

export type WorkbookUiState = "empty" | "loading" | "error" | "success";
export type SqlDialect = "ansi" | "snowflake" | "postgres" | "duckdb" | "bigquery" | "tsql";

interface WorkbookSessionValue {
  state: WorkbookUiState;
  error: string | null;
  fileName: string | null;
  heldFile: File | null;
  result: TranspileResult | null;
  dialect: SqlDialect;
  setDialect: (dialect: SqlDialect) => void;
  fileRef: RefObject<File | null>;
  runFile: (file: File, nextDialect?: SqlDialect) => Promise<boolean>;
}

const WorkbookSessionContext = createContext<WorkbookSessionValue | null>(null);

export function WorkbookProvider({ children }: { children: ReactNode }) {
  const fileRef = useRef<File | null>(null);
  const [state, setState] = useState<WorkbookUiState>("empty");
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [heldFile, setHeldFile] = useState<File | null>(null);
  const [result, setResult] = useState<TranspileResult | null>(null);
  const [dialect, setDialect] = useState<SqlDialect>("ansi");

  const runFile = useCallback(async (file: File, nextDialect: SqlDialect = dialect) => {
    setState("loading");
    setError(null);
    setFileName(file.name);
    setResult(null);
    fileRef.current = file;
    setHeldFile(file);
    const body = new FormData();
    body.append("file", file, file.name);
    body.append("dialect", nextDialect);
    try {
      const response = await fetch("/api/transpile", { method: "POST", body });
      const data: unknown = await response.json();
      if (!response.ok) {
        const message =
          data && typeof data === "object" && "error" in data && typeof data.error === "string"
            ? data.error
            : "Transpile failed.";
        setError(message);
        setState("error");
        return false;
      }
      setResult(data as TranspileResult);
      setState("success");
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error.");
      setState("error");
      return false;
    }
  }, [dialect]);

  return (
    <WorkbookSessionContext.Provider
      value={{ state, error, fileName, heldFile, result, dialect, setDialect, fileRef, runFile }}
    >
      {children}
    </WorkbookSessionContext.Provider>
  );
}

export function useWorkbookSession(): WorkbookSessionValue {
  const session = useContext(WorkbookSessionContext);
  if (!session) {
    throw new Error("Workbook session is missing.");
  }
  return session;
}
