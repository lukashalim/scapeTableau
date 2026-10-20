import { useCallback, useEffect, useMemo, useState } from "react";
import { BrandIcon } from "./components/BrandIcon";
import { DropZone } from "./components/DropZone";
import { SqlPane } from "./components/SqlPane";
import { StatusBar } from "./components/StatusBar";
import type { ConvertStatus, SqlDialect } from "../electron/types";

type Tab = "extract" | "sql";

function fileName(path: string | null): string | null {
  if (!path) return null;
  return path.split(/[/\\]/).pop() || path;
}

export default function App() {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("extract");
  const [status, setStatus] = useState<ConvertStatus>({ phase: "idle" });
  const [dialect, setDialect] = useState<SqlDialect>("ansi");
  const [sqlScript, setSqlScript] = useState("");
  const [copyFlash, setCopyFlash] = useState(false);

  useEffect(() => {
    return window.biescape.onStatus((next) => {
      setStatus(next);
      if (next.phase === "sql_ready") {
        setSqlScript(next.sqlScript);
      }
    });
  }, []);

  const busy =
    status.phase === "converting" || status.phase === "analyzing";

  const selectPath = useCallback((path: string) => {
    setSelectedPath(path);
    setSqlScript("");
    setStatus({ phase: "selected", sourcePath: path });
  }, []);

  const runExtract = useCallback(async () => {
    if (!selectedPath) return;
    const result = await window.biescape.convert(selectedPath);
    setStatus(result);
    if (result.phase === "success" || result.phase === "error") {
      if (result.sourcePath) setSelectedPath(result.sourcePath);
    }
  }, [selectedPath]);

  const runSql = useCallback(async () => {
    if (!selectedPath) return;
    const result = await window.biescape.transpile(selectedPath, dialect);
    setStatus(result);
    if (result.phase === "sql_ready") {
      setSqlScript(result.sqlScript);
    }
  }, [dialect, selectedPath]);

  const onCopy = useCallback(async () => {
    if (!sqlScript) return;
    try {
      await navigator.clipboard.writeText(sqlScript);
      setCopyFlash(true);
      window.setTimeout(() => setCopyFlash(false), 1200);
    } catch {
      setStatus({
        phase: "error",
        sourcePath: selectedPath ?? undefined,
        error: "Could not copy to clipboard.",
      });
    }
  }, [selectedPath, sqlScript]);

  const onSave = useCallback(async () => {
    if (!sqlScript || !selectedPath) return;
    const base = fileName(selectedPath)?.replace(/\.[^.]+$/, "") || "workbook";
    const saved = await window.biescape.saveSql(`${base}.sql`, sqlScript);
    if (saved) {
      setStatus({
        phase: "success",
        sourcePath: selectedPath,
        outputPath: saved,
        sheets: [],
        notes: [],
      });
    }
  }, [selectedPath, sqlScript]);

  const statusLabel = useMemo(() => {
    if (status.phase === "analyzing") return "Analyzing…";
    if (status.phase === "sql_ready") return "SQL ready";
    if (status.phase === "converting") return "Converting…";
    if (status.phase === "success") return `Saved ${status.outputPath}`;
    if (status.phase === "error") return status.error;
    if (status.phase === "selected") return fileName(status.sourcePath) || "Selected";
    return null;
  }, [status]);

  return (
    <div className="app">
      <div className="shell">
        <header className="header">
          <BrandIcon />
          <h1>BIEscape Converter</h1>
        </header>

        <main className="main">
          <DropZone
            busy={busy}
            onPath={selectPath}
            onInvalid={(message) =>
              setStatus({
                phase: "error",
                sourcePath: selectedPath ?? undefined,
                error: message,
              })
            }
          />

          <p className="caption">
            Extract to Excel · Transpile SQL locally · Works offline
          </p>

          <div className="tabs" role="tablist" aria-label="Mode">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "extract"}
              className={`tab${tab === "extract" ? " active" : ""}`}
              onClick={() => setTab("extract")}
            >
              Extract
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "sql"}
              className={`tab${tab === "sql" ? " active" : ""}`}
              onClick={() => setTab("sql")}
            >
              SQL
            </button>
          </div>

          {tab === "extract" && (
            <div className="panel">
              <button
                type="button"
                className="primary"
                disabled={busy || !selectedPath}
                onClick={() => void runExtract()}
              >
                Convert &amp; Save .xlsx…
              </button>
              <p className="caption tight">
                One sheet per table · 100k row cap · Hyper needs a local extract
              </p>
            </div>
          )}

          {tab === "sql" && (
            <SqlPane
              dialect={dialect}
              onDialect={setDialect}
              sql={sqlScript}
              busy={busy}
              hasFile={Boolean(selectedPath)}
              onRun={() => void runSql()}
              onCopy={() => void onCopy()}
              onSave={() => void onSave()}
              copyFlash={copyFlash}
            />
          )}

          {status.phase === "converting" && (
            <div className="banner progress" role="status">
              <span className="spinner" aria-hidden="true" />
              Converting…
            </div>
          )}

          {status.phase === "analyzing" && (
            <div className="banner progress" role="status">
              <span className="spinner" aria-hidden="true" />
              Analyzing…
            </div>
          )}

          {status.phase === "success" && tab === "extract" && (
            <div className="banner success" role="status">
              Saved {status.outputPath}
              {status.sheets.length > 0
                ? ` · ${status.sheets.length} sheet${status.sheets.length === 1 ? "" : "s"}`
                : ""}
            </div>
          )}

          {status.phase === "success" && tab === "sql" && (
            <div className="banner success" role="status">
              Saved {status.outputPath}
            </div>
          )}

          {status.phase === "sql_ready" && (
            <div className="banner success" role="status">
              SQL ready
              {status.skipped.length > 0
                ? ` · ${status.skipped.length} skipped`
                : ""}
            </div>
          )}

          {status.phase === "error" && (
            <div className="banner error" role="alert">
              {status.error}
            </div>
          )}
        </main>

        <StatusBar path={selectedPath} detail={statusLabel} />
      </div>
    </div>
  );
}
