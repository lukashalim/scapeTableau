"use client";

import dynamic from "next/dynamic";
import {
  AlertCircle,
  BookOpen,
  Check,
  Copy,
  Download,
  FileCode2,
  FileSpreadsheet,
  Loader2,
  Printer,
  Search,
  Sparkles,
  Table2,
  Upload,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useCallback, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DESKTOP_RELEASE_URL, DESKTOP_RELEASES_PAGE } from "@/lib/site";
import type {
  CalculatedField,
  ColumnField,
  DashboardInfo,
  ExtractInventory,
  TranspileResult,
} from "@/lib/transpile-types";
import { cn } from "@/lib/utils";

const Editor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[480px] items-center justify-center text-sm text-muted-foreground">
      Loading SQL editor…
    </div>
  ),
});

type UiState = "empty" | "loading" | "error" | "success";
type FieldFilter = "all" | "active" | "hidden";
type SqlDialect = "ansi" | "snowflake" | "postgres" | "duckdb" | "bigquery" | "tsql";

const SQL_DIALECTS: { value: SqlDialect; label: string }[] = [
  { value: "ansi", label: "ANSI" },
  { value: "snowflake", label: "Snowflake" },
  { value: "bigquery", label: "BigQuery" },
  { value: "tsql", label: "SQL Server" },
  { value: "postgres", label: "Postgres" },
  { value: "duckdb", label: "DuckDB" },
];

function statusVariant(status: CalculatedField["status"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "ok") return "default";
  if (status === "unparseable") return "secondary";
  if (status === "error") return "destructive";
  return "outline";
}

function printDashboard(dashboard: DashboardInfo) {
  const html = dashboard.html ?? `<html><body><h1>${dashboard.name}</h1>${dashboard.svg ?? ""}</body></html>`;
  const frame = window.open("", "_blank", "noopener,noreferrer,width=960,height=720");
  if (!frame) {
    toast.error("Pop-up blocked. Allow pop-ups to print the dashboard wireframe.");
    return;
  }
  frame.document.open();
  frame.document.write(html);
  frame.document.close();
  frame.focus();
  frame.print();
}

function ExtractPanel({
  inventory,
  file,
}: {
  inventory: ExtractInventory | null | undefined;
  file: File | null;
}) {
  const [busy, setBusy] = useState(false);
  const downloadXlsx = useCallback(async () => {
    if (!file) {
      toast.error("Re-drop the workbook to download Excel.");
      return;
    }
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      const response = await fetch("/api/extract", { method: "POST", body });
      if (!response.ok) {
        const data: unknown = await response.json().catch(() => null);
        const message =
          data && typeof data === "object" && "error" in data && typeof data.error === "string"
            ? data.error
            : "Extract failed.";
        toast.error(message);
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "biescape-extract.xlsx";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Extract failed.");
    } finally {
      setBusy(false);
    }
  }, [file]);

  if (!inventory) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No extract inventory on this result.</p>;
  }

  const hasHyper = inventory.hyper_files.length > 0;
  const hyperOnly =
    hasHyper && inventory.tabular_files.length === 0 && inventory.custom_sql.length === 0;
  const packagedMembers = inventory.archive_members;
  const hasPackagedMembers = packagedMembers.length > 0;
  const canDownloadXlsx = inventory.tabular_files.length > 0 && Boolean(file);

  return (
    <div className="space-y-4">
      {!hasPackagedMembers ? (
        <p className="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
          No packaged tables in this workbook. Custom SQL still appears below. For Excel extract, use a .twbx
          with CSV/XLS data or the desktop app for .hyper.
        </p>
      ) : null}
      {hasHyper ? (
        <Alert>
          <AlertTitle>{hyperOnly ? "Hyper-only package" : "Hyper extract detected"}</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>
              Hyper extracts need the Tableau Hyper engine and can&apos;t be converted in the browser.
            </p>
            <p>Download the free Windows installer to turn this into Excel on your computer.</p>
            <a
              href={DESKTOP_RELEASE_URL}
              className={cn(buttonVariants({ variant: "default" }), "inline-flex")}
            >
              <Download data-icon="inline-start" />
              Download Windows installer (.exe)
            </a>
            <p className="text-xs text-muted-foreground">
              Direct .exe download (~300MB with hyperd). Unsigned — SmartScreen may warn (More info → Run anyway).
              Release notes:{" "}
              <a
                href={DESKTOP_RELEASES_PAGE}
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-4"
              >
                GitHub Releases
              </a>
              .
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      {hasPackagedMembers
        ? inventory.notes
            .filter((note) => !/listed only|not converted/i.test(note) || !hasHyper)
            .map((note) => (
              <p key={note} className="text-sm text-muted-foreground">
                {note}
              </p>
            ))
        : null}
      {canDownloadXlsx ? (
        <div className="flex justify-end">
          <Button onClick={() => void downloadXlsx()} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" data-icon="inline-start" /> : <Download data-icon="inline-start" />}
            Download .xlsx
          </Button>
        </div>
      ) : null}
      {hasPackagedMembers ? (
        <ScrollArea className="h-[280px] rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Path</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Size</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {packagedMembers.map((member) => (
                <TableRow key={member.path}>
                  <TableCell className="font-mono text-xs">{member.path}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{member.kind}</Badge>
                  </TableCell>
                  <TableCell>{member.size}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </ScrollArea>
      ) : null}
      {inventory.custom_sql.length > 0 ? (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Custom SQL (not executed)</h3>
          {inventory.custom_sql.map((entry, index) => (
            <pre key={`${entry.datasource}-${index}`} className="overflow-auto rounded-xl border bg-muted/40 p-3 font-mono text-xs">
              {entry.datasource ? `-- ${entry.datasource} / ${entry.name ?? "relation"}\n` : ""}
              {entry.sql}
            </pre>
          ))}
        </div>
      ) : null}
      {inventory.hyper_files.length > 0 ? (
        <p className="text-sm text-muted-foreground">
          Hyper files: {inventory.hyper_files.map((item) => item.path).join(", ")}
        </p>
      ) : null}
    </div>
  );
}

function InspectPanel({ result }: { result: TranspileResult }) {
  const inspect = result.inspect;
  const [fieldFilter, setFieldFilter] = useState<FieldFilter>("all");
  const fields: ColumnField[] = inspect?.fields ?? [];
  const filtered = fields.filter((field) => {
    if (fieldFilter === "hidden") return field.hidden;
    if (fieldFilter === "active") return !field.hidden;
    return true;
  });
  const dashboards = result.dashboards?.length ? result.dashboards : inspect?.dashboards ?? [];

  if (!inspect) {
    return <p className="py-8 text-center text-sm text-muted-foreground">Inspect metadata is unavailable.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2 text-sm">
        <Badge>{inspect.counts.fields} fields</Badge>
        <Badge variant="secondary">{inspect.counts.hidden_fields} hidden</Badge>
        <Badge variant="outline">{inspect.counts.calculated_fields} calculated</Badge>
        <Badge variant="outline">{inspect.counts.parameters} parameters</Badge>
        <Badge variant="outline">{inspect.counts.dashboards} dashboards</Badge>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {result.datasources.map((ds) => (
          <Card key={ds.name}>
            <CardHeader>
              <CardTitle className="text-base">{ds.caption || ds.name}</CardTitle>
              <CardDescription className="font-mono text-xs">{ds.name}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {ds.connections.length === 0 ? (
                <p className="text-muted-foreground">No live connection (parameters or bundled).</p>
              ) : (
                ds.connections.map((conn, index) => (
                  <div key={`${ds.name}-${index}`} className="rounded-lg border p-2 font-mono text-xs">
                    <div>class: {conn.class_name ?? "—"}</div>
                    <div>server: {conn.server ?? "—"}</div>
                    <div>database: {conn.dbname ?? "—"}</div>
                    <div>file: {conn.filename ?? "—"}</div>
                    {conn.password ? <div>password: {conn.password}</div> : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">Fields</p>
          <select
            className="h-8 rounded-md border bg-background px-2 text-sm"
            value={fieldFilter}
            onChange={(event) => setFieldFilter(event.target.value as FieldFilter)}
          >
            <option value="all">All</option>
            <option value="active">Active</option>
            <option value="hidden">Hidden</option>
          </select>
        </div>
        <ScrollArea className="h-[320px] rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Caption</TableHead>
                <TableHead>Datasource</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Hidden</TableHead>
                <TableHead>Calculated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((field) => (
                <TableRow key={`${field.datasource}-${field.name}`}>
                  <TableCell className="font-medium">{field.caption}</TableCell>
                  <TableCell>{field.datasource_caption || field.datasource}</TableCell>
                  <TableCell>{field.role}</TableCell>
                  <TableCell>{field.datatype}</TableCell>
                  <TableCell>{field.hidden ? "yes" : ""}</TableCell>
                  <TableCell>{field.calculated ? "yes" : ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </ScrollArea>
      </div>
      <div className="space-y-2">
        <p className="text-sm font-medium">Parameters</p>
        {inspect.parameters.length === 0 ? (
          <p className="text-sm text-muted-foreground">No parameters.</p>
        ) : (
          <ul className="list-inside list-disc text-sm">
            {inspect.parameters.map((param) => (
              <li key={param.name}>
                {param.caption} ({param.datatype ?? "unknown"}) {param.value ? `= ${param.value}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="space-y-3">
        <p className="text-sm font-medium">Dashboards (wireframe — Print to PDF)</p>
        {dashboards.length === 0 ? (
          <p className="text-sm text-muted-foreground">No dashboards in this workbook.</p>
        ) : (
          dashboards.map((dash) => (
            <Card key={dash.name} className="print:border-0 print:shadow-none">
              <CardHeader className="flex flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-base">{dash.name}</CardTitle>
                  <CardDescription>
                    {dash.width ?? "?"} × {dash.height ?? "?"}
                  </CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => printDashboard(dash)}>
                  <Printer data-icon="inline-start" />
                  Print
                </Button>
              </CardHeader>
              <CardContent>
                {dash.svg ? (
                  <div className="overflow-auto" dangerouslySetInnerHTML={{ __html: dash.svg }} />
                ) : (
                  <p className="text-sm text-muted-foreground">No zone layout.</p>
                )}
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}

interface WorkbookConverterProps {
  embedded?: boolean;
}

export function WorkbookConverter({ embedded = false }: WorkbookConverterProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<File | null>(null);
  const { resolvedTheme } = useTheme();
  const [dragOver, setDragOver] = useState(false);
  const [state, setState] = useState<UiState>("empty");
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [heldFile, setHeldFile] = useState<File | null>(null);
  const [result, setResult] = useState<TranspileResult | null>(null);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState(false);
  const [copiedDax, setCopiedDax] = useState(false);
  const [dialect, setDialect] = useState<SqlDialect>("ansi");

  const filtered = useMemo(() => {
    if (!result) return [];
    const q = query.trim().toLowerCase();
    if (!q) return result.calculated_fields;
    return result.calculated_fields.filter((field) =>
      [field.caption, field.datasource, field.formula, field.sql, field.dax, field.status]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [query, result]);

  const runFile = useCallback(async (file: File, nextDialect: SqlDialect = dialect) => {
    setState("loading");
    setError(null);
    setFileName(file.name);
    setResult(null);
    fileRef.current = file;
    setHeldFile(file);
    const body = new FormData();
    body.append("file", file, file.name);
    body.append("dialect", nextDialect);
    try {
      const response = await fetch("/api/transpile", { method: "POST", body });
      const data: unknown = await response.json();
      if (!response.ok) {
        const message =
          data && typeof data === "object" && "error" in data && typeof data.error === "string"
            ? data.error
            : "Transpile failed.";
        setError(message);
        setState("error");
        return;
      }
      setResult(data as TranspileResult);
      setState("success");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error.");
      setState("error");
    }
  }, [dialect]);

  const onFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (!file) return;
      const lower = file.name.toLowerCase();
      if (!lower.endsWith(".twb") && !lower.endsWith(".twbx")) {
        setError("Drop a .twb or .twbx workbook.");
        setState("error");
        return;
      }
      void runFile(file);
    },
    [runFile],
  );

  const trySample = useCallback(async () => {
    setState("loading");
    setError(null);
    setFileName("superstore_calcs.twb");
    try {
      const response = await fetch("/samples/superstore_calcs.twb");
      if (!response.ok) {
        throw new Error("Sample workbook is missing from the web public folder.");
      }
      const blob = await response.blob();
      const file = new File([blob], "superstore_calcs.twb", { type: "application/xml" });
      await runFile(file);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the sample workbook.");
      setState("error");
    }
  }, [runFile]);

  const copySql = useCallback(async () => {
    if (!result?.sql_script) return;
    await navigator.clipboard.writeText(result.sql_script);
    setCopied(true);
    toast.success("SQL copied to clipboard");
    window.setTimeout(() => setCopied(false), 1600);
  }, [result]);

  const copyDax = useCallback(async () => {
    if (!result?.dax_script) return;
    await navigator.clipboard.writeText(result.dax_script);
    setCopiedDax(true);
    toast.success("DAX copied to clipboard");
    window.setTimeout(() => setCopiedDax(false), 1600);
  }, [result]);

  const downloadMarkdown = useCallback(() => {
    if (!result) return;
    const blob = new Blob([result.markdown_audit], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "escapetableau-audit.md";
    a.click();
    URL.revokeObjectURL(url);
  }, [result]);

  const downloadPbip = useCallback(async () => {
    if (!heldFile) {
      toast.error("Re-drop the workbook to download the Power BI project.");
      return;
    }
    try {
      const body = new FormData();
      body.append("file", heldFile, heldFile.name);
      body.append("dialect", dialect);
      const response = await fetch("/api/pbip", { method: "POST", body });
      if (!response.ok) {
        const raw = await response.text();
        let message = `PBIP export failed (HTTP ${response.status})`;
        try {
          const data: unknown = JSON.parse(raw);
          if (data && typeof data === "object" && "error" in data && typeof data.error === "string") {
            message = data.error;
          }
        } catch {
          const stripped = raw.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
          if (stripped) message = stripped;
        }
        const truncated = message.slice(0, 280);
        console.error("[pbip download]", response.status, truncated);
        toast.error(truncated);
        return;
      }
      const blob = await response.blob();
      if (blob.size < 4) {
        toast.error("PBIP export returned an empty file.");
        return;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const base = (fileName || heldFile.name).replace(/\.(twbx?)$/i, "");
      a.download = `${base || "escapetableau"}.pbip.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Downloaded Power BI project (.pbip.zip) — unzip and open the .pbip in Desktop");
    } catch (err) {
      const message = err instanceof Error ? err.message : "PBIP download failed.";
      console.error("[pbip download]", message);
      toast.error(message.slice(0, 280));
    }
  }, [heldFile, fileName, dialect]);

  const okCount = result?.calculated_fields.filter((f) => f.status === "ok").length ?? 0;

  return (
    <div
      className={cn(
        "mx-auto flex w-full max-w-6xl flex-col gap-8 px-4",
        embedded ? "pb-10 pt-6 md:pb-16 md:pt-8" : "py-10 md:py-16",
      )}
    >
      {embedded ? null : (
        <header className="max-w-3xl space-y-2">
          <h2 className="font-heading text-2xl font-semibold tracking-tight">Convert a workbook</h2>
          <p className="text-sm text-muted-foreground">
            Drop a <span className="font-medium text-foreground">.twb</span> or{" "}
            <span className="font-medium text-foreground">.twbx</span>. One pass transpiles SQL, extracts packaged
            tables, and inspects schema.
          </p>
        </header>
      )}

      <Card className="ring-foreground/15">
        <CardHeader>
          <CardTitle>Workbook</CardTitle>
          <CardDescription>Accepts Tableau Desktop workbooks. Try the synthetic Superstore sample offline.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label
            htmlFor="workbook-file"
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragOver(false);
              onFiles(event.dataTransfer.files);
            }}
            className={cn(
              "flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-14 text-center transition-colors",
              dragOver ? "border-primary bg-muted/60" : "border-border hover:bg-muted/40",
            )}
          >
            {state === "loading" ? (
              <Loader2 className="size-8 animate-spin text-muted-foreground" />
            ) : (
              <Upload className="size-8 text-muted-foreground" />
            )}
            <div className="space-y-1">
              <p className="font-medium">Drop a .twb or .twbx here</p>
              <p className="text-sm text-muted-foreground">or click to browse. Files are not persisted.</p>
            </div>
          </label>
          <input
            ref={inputRef}
            id="workbook-file"
            type="file"
            accept=".twb,.twbx,application/xml,application/zip"
            className="sr-only"
            onChange={(event) => {
              onFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
            <button
              type="button"
              className={buttonVariants()}
              disabled={state === "loading"}
              onClick={() => void trySample()}
            >
              <Sparkles data-icon="inline-start" />
              Try with Sample Workbook
            </button>
            <a
              href={DESKTOP_RELEASE_URL}
              className={cn(buttonVariants({ variant: "outline" }), "inline-flex")}
            >
              <Download data-icon="inline-start" />
              Need Hyper → Excel? Download the Windows desktop app (.exe)
            </a>
            {fileName ? (
              <p className="text-sm text-muted-foreground">
                Last file: <span className="text-foreground">{fileName}</span>
              </p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {state === "loading" ? (
        <Card className="bg-muted/20">
          <CardContent className="flex items-center gap-3 py-10 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Transpiling calculated fields with the local Python engine…
          </CardContent>
        </Card>
      ) : null}

      {state === "error" ? (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertTitle>Could not convert this workbook</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {state === "success" && result ? (
        <div className="space-y-4 rounded-xl bg-muted/15 p-4 ring-1 ring-foreground/10 md:p-5">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge>{result.calculated_fields.length} fields</Badge>
            <Badge variant="secondary">{okCount} ok</Badge>
            <Badge variant="outline">{result.datasources.length} datasources</Badge>
            {result.workbook_name ? (
              <span className="text-muted-foreground">{result.workbook_name}</span>
            ) : null}
          </div>
          <Tabs defaultValue="transpile">
            <div className="rounded-lg border bg-background/80 p-2">
              <TabsList className="h-auto w-full flex-wrap justify-start">
                <TabsTrigger value="transpile">
                  <FileCode2 data-icon="inline-start" />
                  Transpile to SQL/dbt
                </TabsTrigger>
                <TabsTrigger value="extract">
                  <FileSpreadsheet data-icon="inline-start" />
                  Extract Data
                </TabsTrigger>
                <TabsTrigger value="inspect">
                  <Search data-icon="inline-start" />
                  Inspect Schema
                </TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="transpile" className="mt-3">
              <Tabs defaultValue="sql">
                <TabsList>
                  <TabsTrigger value="sql">
                    <FileCode2 data-icon="inline-start" />
                    SQL
                  </TabsTrigger>
                  <TabsTrigger value="dax">
                    <FileCode2 data-icon="inline-start" />
                    DAX
                  </TabsTrigger>
                  <TabsTrigger value="audit">
                    <Table2 data-icon="inline-start" />
                    Audit
                  </TabsTrigger>
                  <TabsTrigger value="markdown">
                    <BookOpen data-icon="inline-start" />
                    Markdown
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="sql" className="mt-3 space-y-2">
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    <label className="flex items-center gap-2 text-sm text-muted-foreground">
                      Dialect
                      <select
                        className="rounded-md border bg-background px-2 py-1 text-foreground"
                        value={dialect}
                        onChange={(e) => {
                          const next = e.target.value as SqlDialect;
                          setDialect(next);
                          if (heldFile) void runFile(heldFile, next);
                        }}
                      >
                        {SQL_DIALECTS.map((d) => (
                          <option key={d.value} value={d.value}>
                            {d.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <Button size="sm" onClick={() => void downloadPbip()}>
                      <Download data-icon="inline-start" />
                      Download .pbip
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => void copySql()}>
                      {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
                      {copied ? "Copied" : "Copy SQL"}
                    </Button>
                  </div>
                  <div className="overflow-hidden rounded-xl border">
                    <Editor
                      height="480px"
                      defaultLanguage="sql"
                      value={result.sql_script}
                      theme={resolvedTheme === "dark" ? "vs-dark" : "light"}
                      options={{
                        readOnly: true,
                        minimap: { enabled: false },
                        fontSize: 13,
                        scrollBeyondLastLine: false,
                        wordWrap: "on",
                      }}
                    />
                  </div>
                </TabsContent>
                <TabsContent value="dax" className="mt-3 space-y-2">
                  <div className="flex justify-end">
                    <Button variant="outline" size="sm" onClick={() => void copyDax()} disabled={!result.dax_script}>
                      {copiedDax ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
                      {copiedDax ? "Copied" : "Copy"}
                    </Button>
                  </div>
                  <div className="overflow-hidden rounded-xl border">
                    <Editor
                      height="480px"
                      defaultLanguage="sql"
                      value={result.dax_script ?? "// No DAX in this result"}
                      theme={resolvedTheme === "dark" ? "vs-dark" : "light"}
                      options={{
                        readOnly: true,
                        minimap: { enabled: false },
                        fontSize: 13,
                        scrollBeyondLastLine: false,
                        wordWrap: "on",
                      }}
                    />
                  </div>
                </TabsContent>
                <TabsContent value="audit" className="mt-3 space-y-3">
                  <Input
                    placeholder="Search caption, formula, SQL, DAX, status…"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                  {filtered.length === 0 ? (
                    <p className="py-8 text-center text-sm text-muted-foreground">No calculated fields match that search.</p>
                  ) : (
                    <ScrollArea className="h-[480px] rounded-xl border">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Caption</TableHead>
                            <TableHead>Datasource</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead>Tableau</TableHead>
                            <TableHead>SQL</TableHead>
                            <TableHead>DAX</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {filtered.map((field) => (
                            <TableRow key={`${field.datasource}-${field.name}`}>
                              <TableCell className="font-medium">{field.caption}</TableCell>
                              <TableCell>{field.datasource}</TableCell>
                              <TableCell>
                                <Badge variant={statusVariant(field.status)}>{field.status}</Badge>
                              </TableCell>
                              <TableCell className="max-w-[220px] truncate font-mono text-xs" title={field.formula}>
                                {field.formula}
                              </TableCell>
                              <TableCell className="max-w-[280px] truncate font-mono text-xs" title={field.sql}>
                                {field.sql}
                              </TableCell>
                              <TableCell className="max-w-[280px] truncate font-mono text-xs" title={field.dax ?? ""}>
                                {field.dax}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </ScrollArea>
                  )}
                </TabsContent>
                <TabsContent value="markdown" className="mt-3 space-y-2">
                  <div className="flex justify-end">
                    <Button variant="outline" size="sm" onClick={downloadMarkdown}>
                      <Download data-icon="inline-start" />
                      Download .md
                    </Button>
                  </div>
                  <pre className="max-h-[480px] overflow-auto rounded-xl border bg-muted/40 p-4 font-mono text-xs leading-relaxed">
                    {result.markdown_audit}
                  </pre>
                </TabsContent>
              </Tabs>
            </TabsContent>
            <TabsContent value="extract" className="mt-3">
              <ExtractPanel inventory={result.extract_inventory} file={heldFile} />
            </TabsContent>
            <TabsContent value="inspect" className="mt-3">
              <InspectPanel result={result} />
            </TabsContent>
          </Tabs>
        </div>
      ) : null}

      <Separator />
      <footer className="space-y-2 pb-8 text-sm text-muted-foreground">
        <p>
          Clone the repo:{" "}
          <a
            href="https://github.com/lukashalim/scapeTableau"
            className="underline underline-offset-4"
            target="_blank"
            rel="noreferrer"
          >
            https://github.com/lukashalim/scapeTableau
          </a>
        </p>
        <p>
          CLI:{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
            python cli.py process workbook.twb --output-dir ./output --format all --print-summary
          </code>
        </p>
        <p>
          Inspect / extract:{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">python cli.py inspect workbook.twb</code>{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
            python cli.py extract workbook.twbx --output extract.xlsx
          </code>
        </p>
        <p>Requires Python 3.11+ and <code className="font-mono text-xs">pip install -r requirements.txt</code>.</p>
      </footer>
    </div>
  );
}
