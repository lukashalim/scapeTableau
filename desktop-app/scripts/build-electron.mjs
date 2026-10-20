import * as esbuild from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

await esbuild.build({
  entryPoints: [path.join(root, "electron/main.ts")],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: path.join(root, "dist-electron/main.js"),
  external: ["electron"],
  sourcemap: true,
  target: "node20",
});

await esbuild.build({
  entryPoints: [path.join(root, "electron/preload.ts")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: path.join(root, "dist-electron/preload.cjs"),
  external: ["electron"],
  sourcemap: true,
  target: "node20",
});

console.log("electron main/preload built → dist-electron/");
