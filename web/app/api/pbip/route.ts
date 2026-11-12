import { spawn } from "node:child_process";
import path from "node:path";

import { NextRequest, NextResponse } from "next/server";

const PBIP_TYPE = "application/zip";

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

/** Resolve Python engine URL for PBIP (mirrors extract → TRANSPILER_URL rewrite). */
function pbipEngineUrl(request: NextRequest): URL {
  const dedicated = process.env.PBIP_URL?.trim();
  if (dedicated) {
    return new URL(dedicated);
  }
  const configured = process.env.TRANSPILER_URL?.trim();
  if (configured) {
    const url = new URL(configured);
    url.pathname = url.pathname.replace(/\/transpile\/?$/, "/pbip");
    if (url.pathname.includes("transpile")) {
      url.pathname = "/api/pbip";
    }
    if (!url.pathname.endsWith("/pbip") && !url.pathname.endsWith("/api/pbip")) {
      url.pathname = "/api/pbip";
    }
    return url;
  }
  return new URL("/api/pbip", request.url);
}

function runCliPbip(payload: Buffer): Promise<{ code: number; stdout: Buffer; stderr: string }> {
  const python = process.env.PYTHON_BIN ?? "python3";
  const cli = path.join(repoRoot(), "cli.py");

  return new Promise((resolve, reject) => {
    // `-o -` writes zip bytes to stdout (see cli.py pbip)
    const child = spawn(
      /* turbopackIgnore: true */ python,
      [cli, "pbip", "-", "-o", "-"],
      {
        cwd: repoRoot(),
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
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
  const engineUrl = pbipEngineUrl(request);
  console.info("[api/pbip] forwarding to", engineUrl.toString());
  let forwarded: Response;
  try {
    forwarded = await fetch(engineUrl, {
      method: "POST",
      body: form,
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Engine fetch failed.";
    console.error("[api/pbip] engine fetch error:", message);
    return NextResponse.json({ error: `PBIP engine unreachable: ${message}`.slice(0, 400) }, { status: 502 });
  }

  const buf = new Uint8Array(await forwarded.arrayBuffer());
  const contentType = forwarded.headers.get("Content-Type") ?? PBIP_TYPE;

  if (!forwarded.ok) {
    const snippet = new TextDecoder().decode(buf.slice(0, 500));
    console.error("[api/pbip] engine status", forwarded.status, snippet.slice(0, 300));
    // Prefer JSON error body from engine; otherwise wrap plaintext/HTML
    if (contentType.includes("application/json")) {
      return new NextResponse(buf, {
        status: forwarded.status,
        headers: { "Content-Type": "application/json; charset=utf-8" },
      });
    }
    const text = snippet.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    return NextResponse.json(
      { error: (text || `PBIP engine HTTP ${forwarded.status}`).slice(0, 400) },
      { status: forwarded.status >= 400 ? forwarded.status : 502 },
    );
  }

  return new NextResponse(buf, {
    status: 200,
    headers: {
      "Content-Type": contentType.includes("zip") ? PBIP_TYPE : contentType,
      "Content-Disposition":
        forwarded.headers.get("Content-Disposition") ??
        'attachment; filename="escapetableau.pbip.zip"',
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
    const dialect = form.get("dialect");
    if (typeof dialect === "string" && dialect) {
      retry.set("dialect", dialect);
    }
    return runPythonEngine(request, retry);
  }

  let ran: { code: number; stdout: Buffer; stderr: string };
  try {
    ran = await runCliPbip(payload);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to start the Python CLI.";
    console.error("[api/pbip] CLI spawn error:", message);
    return NextResponse.json({ error: message.slice(0, 400) }, { status: 500 });
  }

  if (ran.code !== 0) {
    const detail = (ran.stderr.trim() || "Python PBIP export failed.").slice(0, 400);
    console.error("[api/pbip] CLI failed:", detail);
    return NextResponse.json({ error: detail }, { status: 400 });
  }

  if (ran.stdout.length < 4 || ran.stdout[0] !== 0x50 || ran.stdout[1] !== 0x4b) {
    const detail = "PBIP CLI did not return a zip (check -o - stdout support).";
    console.error("[api/pbip]", detail, "bytes", ran.stdout.length);
    return NextResponse.json({ error: detail }, { status: 500 });
  }

  return new NextResponse(new Uint8Array(ran.stdout), {
    status: 200,
    headers: {
      "Content-Type": PBIP_TYPE,
      "Content-Disposition": 'attachment; filename="escapetableau.pbip.zip"',
    },
  });
}
