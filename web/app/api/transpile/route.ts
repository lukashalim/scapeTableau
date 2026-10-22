import { spawn } from "node:child_process";
import path from "node:path";

import { NextRequest, NextResponse } from "next/server";

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

const DIALECTS = new Set(["ansi", "snowflake", "postgres", "duckdb", "bigquery", "tsql", "dax"]);

function runCli(
  payload: Buffer,
  dialect: string,
): Promise<{ code: number; stdout: string; stderr: string }> {
  const python = process.env.PYTHON_BIN ?? "python3";
  const cli = path.join(repoRoot(), "cli.py");
  const args = [cli, "process", "-", "--format", "json", "--dialect", dialect];

  return new Promise((resolve, reject) => {
    const child = spawn(/* turbopackIgnore: true */ python, args, {
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
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      });
    });
    child.stdin.write(payload);
    child.stdin.end();
  });
}

async function runPythonEngine(request: NextRequest, form: FormData): Promise<Response> {
  const configured = process.env.TRANSPILER_URL?.trim();
  const engineUrl = configured ? new URL(configured) : new URL("/api/engine", request.url);
  const forwarded = await fetch(engineUrl, {
    method: "POST",
    body: form,
    headers: {
      Accept: "application/json",
    },
    cache: "no-store",
  });
  const text = await forwarded.text();
  return new NextResponse(text, {
    status: forwarded.status,
    headers: { "Content-Type": forwarded.headers.get("Content-Type") ?? "application/json" },
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
  const rawDialect = form.get("dialect");
  const dialect =
    typeof rawDialect === "string" && DIALECTS.has(rawDialect.toLowerCase())
      ? rawDialect.toLowerCase()
      : "ansi";

  const payload = Buffer.from(await file.arrayBuffer());
  if (payload.length === 0) {
    return NextResponse.json({ error: "File is empty." }, { status: 400 });
  }

  if (!useCliTranspiler()) {
    const retry = new FormData();
    retry.set("file", new File([payload], file.name, { type: file.type || "application/octet-stream" }));
    retry.set("dialect", dialect);
    return runPythonEngine(request, retry);
  }

  let ran: { code: number; stdout: string; stderr: string };
  try {
    ran = await runCli(payload, dialect);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to start the Python CLI.";
    return NextResponse.json({ error: message }, { status: 500 });
  }

  if (ran.code !== 0) {
    const detail = ran.stderr.trim() || ran.stdout.trim() || "Python transpiler failed.";
    return NextResponse.json({ error: detail }, { status: 400 });
  }

  try {
    const parsed: unknown = JSON.parse(ran.stdout);
    return NextResponse.json(parsed);
  } catch {
    return NextResponse.json(
      { error: "The CLI did not return JSON. Is Python 3.11+ installed with requirements.txt?" },
      { status: 500 },
    );
  }
}
