import { contextBridge, ipcRenderer, webUtils } from "electron";
import type { ConvertStatus, SqlDialect } from "./types.js";

const api = {
  selectFile: (): Promise<string | null> =>
    ipcRenderer.invoke("dialog:openExtract"),
  convert: (sourcePath: string): Promise<ConvertStatus> =>
    ipcRenderer.invoke("convert:run", sourcePath),
  transpile: (
    sourcePath: string,
    dialect: SqlDialect,
  ): Promise<ConvertStatus> =>
    ipcRenderer.invoke("transpile:run", sourcePath, dialect),
  saveSql: (defaultName: string, content: string): Promise<string | null> =>
    ipcRenderer.invoke("dialog:saveSql", defaultName, content),
  getPathForFile: (file: File): string => webUtils.getPathForFile(file),
  onStatus: (cb: (status: ConvertStatus) => void): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, status: ConvertStatus) =>
      cb(status);
    ipcRenderer.on("convert:status", handler);
    return () => ipcRenderer.removeListener("convert:status", handler);
  },
};

contextBridge.exposeInMainWorld("biescape", api);

export type BiescapeApi = typeof api;
