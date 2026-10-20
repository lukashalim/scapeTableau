import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { app } from "electron";

export type ConvertResult =
  | {
      ok: true;
      output: string;
      sheets: string[];
      notes: string[];
      hyper_files: number;
    }
  | { ok: false; error: string; traceback?: string };

export type TranspileResult =
  | {
      ok: true;
      sql_script: string;
      workbook: string | null;
      dialect: string;
      skipped: { caption: string; status: string; error?: string | null }[];
      field_count: number;
      error: null;
    }
  | { ok: false; error: string; traceback?: string };

function repoRoot(): string {
  return path.resolve(app.getAppPath(), "..");
}

/** Resolve the Python sidecar (packaged exe, local PyInstaller build, or python script). */
export function resolveSidecar(): { command: string; argsPrefix: string[] } {
  const resources = process.resourcesPath;
  const packaged = path.join(
    resources,
    "biescape-convert",
    process.platform === "win32" ? "biescape-convert.exe" : "biescape-convert",
  );
  if (app.isPackaged && fs.existsSync(packaged)) {
    return { command: packaged, argsPrefix: [] };
  }

  const localDist = path.join(
    repoRoot(),
    "dist",
    "biescape-convert",
    process.platform === "win32" ? "biescape-convert.exe" : "biescape-convert",
  );
  if (fs.existsSync(localDist)) {
    return { command: localDist, argsPrefix: [] };
  }

  const script = path.join(repoRoot(), "desktop", "convert_cli.py");
  const python =
    process.env.BIESCAPE_PYTHON ||
    (process.platform === "win32" ? "python" : "python3");
  return { command: python, argsPrefix: [script] };
}

function runSidecar(args: string[]): Promise<unknown> {
  const { command, argsPrefix } = resolveSidecar();
  const fullArgs = [...argsPrefix, ...args];

  return new Promise((resolve) => {
    const child = spawn(command, fullArgs, {
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });

    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });

    child.on("error", (err) => {
      resolve({
        ok: false,
        error: `Failed to start converter: ${err.message}`,
      });
    });

    child.on("close", (code) => {
      const lines = stdout
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean);
      const last = lines[lines.length - 1];
      if (last) {
        try {
          resolve(JSON.parse(last));
          return;
        } catch {
          // fall through
        }
      }
      const detail = stderr.trim() || stdout.trim() || `exit ${code ?? "?"}`;
      resolve({ ok: false, error: detail });
    });
  });
}

export function runConvert(
  sourcePath: string,
  outputPath: string,
): Promise<ConvertResult> {
  return runSidecar(["convert", sourcePath, outputPath]) as Promise<ConvertResult>;
}

export function runTranspile(
  sourcePath: string,
  dialect: string,
): Promise<TranspileResult> {
  return runSidecar([
    "transpile",
    sourcePath,
    "--dialect",
    dialect,
  ]) as Promise<TranspileResult>;
}
