import { spawn } from "node:child_process";
import path from "node:path";

import { NextRequest, NextResponse } from "next/server";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function repoRoot(): string {
  return path.resolve(process.cwd(), "..");
}

function useCliTranspiler(): boolean {
  if (process.env.SCAPE_TABLEAU_USE_CLI === "1") {
    return true;
  }
  if (process.env.SCAPE_TABLEAU_USE_CLI === "0") {
    return false;
  }
  return !process.env.VERCEL;
}

function extractEngineUrl(request: NextRequest): URL {
  const dedicated = process.env.EXTRACT_URL?.trim();
  if (dedicated) {
    return new URL(dedicated);
  }
  const configured = process.env.TRANSPILER_URL?.trim();
  if (configured) {
    const url = new URL(configured);
    url.pathname = url.pathname.replace(/\/transpile\/?$/, "/extract");
    if (url.pathname.includes("transpile")) {
      url.pathname = "/api/extract";
    }
    return url;
  }
  return new URL("/api/extract", request.url);
}

function runCliExtract(payload: Buffer): Promise<{ code: number; stdout: Buffer; stderr: string }> {
  const python = process.env.PYTHON_BIN ?? "python3";
  const cli = path.join(repoRoot(), "cli.py");

  return new Promise((resolve, reject) => {
    const child = spawn(/* turbopackIgnore: true */ python, [cli, "extract", "-"], {
      cwd: repoRoot(),
      env: process.env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      resolve({
        code: code ?? 1,
        stdout: Buffer.concat(stdout),
        stderr: Buffer.concat(stderr).toString("utf8"),
      });
    });
    child.stdin.write(payload);
    child.stdin.end();
  });
}

async function runPythonEngine(request: NextRequest, form: FormData): Promise<Response> {
  const engineUrl = extractEngineUrl(request);
  const forwarded = await fetch(engineUrl, {
    method: "POST",
    body: form,
    cache: "no-store",
  });
  const buf = new Uint8Array(await forwarded.arrayBuffer());
  const contentType = forwarded.headers.get("Content-Type") ?? XLSX_TYPE;
  return new NextResponse(buf, {
    status: forwarded.status,
    headers: {
      "Content-Type": contentType,
      "Content-Disposition": forwarded.headers.get("Content-Disposition") ??
        'attachment; filename="scapetableau-extract.xlsx"',
    },
  });
}

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const file = form.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ error: "Upload a .twb or .twbx file." }, { status: 400 });
  }
  const name = file.name.toLowerCase();
  if (!name.endsWith(".twb") && !name.endsWith(".twbx")) {
    return NextResponse.json({ error: "Only .twb and .twbx workbooks are accepted." }, { status: 400 });
  }

  const payload = Buffer.from(await file.arrayBuffer());
  if (payload.length === 0) {
    return NextResponse.json({ error: "File is empty." }, { status: 400 });
  }

  if (!useCliTranspiler()) {
    const retry = new FormData();
    retry.set("file", new File([payload], file.name, { type: file.type || "application/octet-stream" }));
    return runPythonEngine(request, retry);
  }

  let ran: { code: number; stdout: Buffer; stderr: string };
  try {
    ran = await runCliExtract(payload);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to start the Python CLI.";
    return NextResponse.json({ error: message }, { status: 500 });
  }

  if (ran.code !== 0) {
    const detail = ran.stderr.trim() || "Python extract failed.";
    return NextResponse.json({ error: detail }, { status: 400 });
  }

  return new NextResponse(new Uint8Array(ran.stdout), {
    status: 200,
    headers: {
      "Content-Type": XLSX_TYPE,
      "Content-Disposition": 'attachment; filename="scapetableau-extract.xlsx"',
    },
  });
}
