import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  shell,
} from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  runConvert,
  runTranspile,
  type ConvertResult,
  type TranspileResult,
} from "./convert.js";
import type { ConvertStatus, SqlDialect } from "./types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const isDev = !app.isPackaged;
const ALLOWED = new Set([".hyper", ".twbx", ".tdsx", ".twb"]);
const EXTRACT_ALLOWED = new Set([".hyper", ".twbx", ".tdsx", ".twb"]);
const DIALECTS = new Set(["ansi", "snowflake", "postgres", "duckdb"]);

let mainWindow: BrowserWindow | null = null;

function sendStatus(status: ConvertStatus): void {
  mainWindow?.webContents.send("convert:status", status);
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 920,
    height: 680,
    minWidth: 720,
    minHeight: 520,
    title: "BIEscape Converter",
    backgroundColor: "#eceef2",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.setMenuBarVisibility(false);

  if (isDev) {
    void mainWindow.loadURL("http://127.0.0.1:5173");
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
}

function defaultOutputPath(sourcePath: string, ext: string): string {
  const parsed = path.parse(sourcePath);
  return path.join(parsed.dir, `${parsed.name}${ext}`);
}

ipcMain.handle("dialog:openExtract", async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Open Tableau workbook or extract",
    properties: ["openFile"],
    filters: [
      {
        name: "Tableau files",
        extensions: ["twb", "twbx", "hyper", "tdsx"],
      },
      { name: "All files", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

ipcMain.handle(
  "dialog:saveSql",
  async (_event, defaultName: string, content: string): Promise<string | null> => {
    if (!mainWindow) return null;
    const save = await dialog.showSaveDialog(mainWindow, {
      title: "Save SQL script",
      defaultPath: defaultName,
      filters: [{ name: "SQL", extensions: ["sql"] }],
    });
    if (save.canceled || !save.filePath) return null;
    const out = save.filePath.endsWith(".sql")
      ? save.filePath
      : `${save.filePath}.sql`;
    fs.writeFileSync(out, content, "utf8");
    return out;
  },
);

ipcMain.handle(
  "convert:run",
  async (_event, sourcePath: string): Promise<ConvertStatus> => {
    if (!sourcePath || typeof sourcePath !== "string") {
      const status: ConvertStatus = {
        phase: "error",
        error: "No file selected.",
      };
      sendStatus(status);
      return status;
    }

    const ext = path.extname(sourcePath).toLowerCase();
    if (!EXTRACT_ALLOWED.has(ext)) {
      const status: ConvertStatus = {
        phase: "error",
        sourcePath,
        error: "Choose a .hyper, .twbx, .tdsx, or .twb file.",
      };
      sendStatus(status);
      return status;
    }

    if (!mainWindow) {
      return { phase: "error", error: "Window not ready." };
    }

    const save = await dialog.showSaveDialog(mainWindow, {
      title: "Save Excel workbook",
      defaultPath: defaultOutputPath(sourcePath, ".xlsx"),
      filters: [{ name: "Excel", extensions: ["xlsx"] }],
    });
    if (save.canceled || !save.filePath) {
      const status: ConvertStatus = { phase: "selected", sourcePath };
      sendStatus(status);
      return status;
    }

    const outputPath = save.filePath.endsWith(".xlsx")
      ? save.filePath
      : `${save.filePath}.xlsx`;

    sendStatus({ phase: "converting", sourcePath, outputPath });

    const result: ConvertResult = await runConvert(sourcePath, outputPath);
    if (!result.ok) {
      const status: ConvertStatus = {
        phase: "error",
        sourcePath,
        error: result.error,
      };
      sendStatus(status);
      return status;
    }

    const status: ConvertStatus = {
      phase: "success",
      sourcePath,
      outputPath: result.output,
      sheets: result.sheets,
      notes: result.notes,
    };
    sendStatus(status);
    return status;
  },
);

ipcMain.handle(
  "transpile:run",
  async (
    _event,
    sourcePath: string,
    dialect: SqlDialect,
  ): Promise<ConvertStatus> => {
    if (!sourcePath || typeof sourcePath !== "string") {
      const status: ConvertStatus = {
        phase: "error",
        error: "No file selected.",
      };
      sendStatus(status);
      return status;
    }
    const ext = path.extname(sourcePath).toLowerCase();
    if (!ALLOWED.has(ext)) {
      const status: ConvertStatus = {
        phase: "error",
        sourcePath,
        error: "Choose a .twb, .twbx, .tdsx, or .hyper file.",
      };
      sendStatus(status);
      return status;
    }
    const d = (dialect || "ansi").toLowerCase();
    if (!DIALECTS.has(d)) {
      const status: ConvertStatus = {
        phase: "error",
        sourcePath,
        error: `Unsupported dialect: ${dialect}`,
      };
      sendStatus(status);
      return status;
    }

    sendStatus({ phase: "analyzing", sourcePath, dialect: d });
    const result: TranspileResult = await runTranspile(sourcePath, d);
    if (!result.ok) {
      const status: ConvertStatus = {
        phase: "error",
        sourcePath,
        error: result.error,
      };
      sendStatus(status);
      return status;
    }

    const status: ConvertStatus = {
      phase: "sql_ready",
      sourcePath,
      dialect: result.dialect,
      sqlScript: result.sql_script,
      workbook: result.workbook,
      skipped: result.skipped,
    };
    sendStatus(status);
    return status;
  },
);

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
