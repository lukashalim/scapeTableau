import { useCallback, useEffect, useState } from "react";
import { BrandIcon } from "./components/BrandIcon";
import { DropZone } from "./components/DropZone";
import { StatusBar } from "./components/StatusBar";
import type { ConvertStatus } from "../electron/types";

export default function App() {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [status, setStatus] = useState<ConvertStatus>({ phase: "idle" });

  useEffect(() => {
    return window.biescape.onStatus(setStatus);
  }, []);

  const runConvert = useCallback(async (sourcePath: string) => {
    setSelectedPath(sourcePath);
    setStatus({ phase: "converting", sourcePath });
    const result = await window.biescape.convert(sourcePath);
    setStatus(result);
    if (result.phase === "success" || result.phase === "error") {
      if (result.sourcePath) setSelectedPath(result.sourcePath);
    }
  }, []);

  const busy = status.phase === "converting";

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
            onPath={(path) => void runConvert(path)}
            onInvalid={(message) =>
              setStatus({ phase: "error", sourcePath: selectedPath ?? undefined, error: message })
            }
          />

          <p className="caption">
            One sheet per table · 100k row cap · Works offline
          </p>

          {status.phase === "converting" && (
            <div className="banner progress" role="status">
              <span className="spinner" aria-hidden="true" />
              Converting…
            </div>
          )}

          {status.phase === "success" && (
            <div className="banner success" role="status">
              Saved {status.outputPath}
              {status.sheets.length > 0
                ? ` · ${status.sheets.length} sheet${status.sheets.length === 1 ? "" : "s"}`
                : ""}
            </div>
          )}

          {status.phase === "error" && (
            <div className="banner error" role="alert">
              {status.error}
            </div>
          )}
        </main>

        <StatusBar path={selectedPath} />
      </div>
    </div>
  );
}
