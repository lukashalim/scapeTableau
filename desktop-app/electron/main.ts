import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  shell,
} from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runConvert, type ConvertResult } from "./convert.js";
import type { ConvertStatus } from "./types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const isDev = !app.isPackaged;
const ALLOWED = new Set([".hyper", ".twbx", ".tdsx"]);

let mainWindow: BrowserWindow | null = null;

function sendStatus(status: ConvertStatus): void {
  mainWindow?.webContents.send("convert:status", status);
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 880,
    height: 620,
    minWidth: 720,
    minHeight: 480,
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

function defaultOutputPath(sourcePath: string): string {
  const parsed = path.parse(sourcePath);
  return path.join(parsed.dir, `${parsed.name}.xlsx`);
}

ipcMain.handle("dialog:openExtract", async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Open Tableau extract",
    properties: ["openFile"],
    filters: [
      { name: "Tableau extracts", extensions: ["hyper", "twbx", "tdsx"] },
      { name: "All files", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

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
    if (!ALLOWED.has(ext)) {
      const status: ConvertStatus = {
        phase: "error",
        sourcePath,
        error: "Choose a .hyper, .twbx, or .tdsx file.",
      };
      sendStatus(status);
      return status;
    }

    if (!mainWindow) {
      return { phase: "error", error: "Window not ready." };
    }

    const save = await dialog.showSaveDialog(mainWindow, {
      title: "Save Excel workbook",
      defaultPath: defaultOutputPath(sourcePath),
      filters: [{ name: "Excel", extensions: ["xlsx"] }],
    });
    if (save.canceled || !save.filePath) {
      const status: ConvertStatus = { phase: "idle" };
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

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
