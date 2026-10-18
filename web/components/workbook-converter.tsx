"use client";

import dynamic from "next/dynamic";
import {
  AlertCircle,
  BookOpen,
  Check,
  Copy,
  Download,
  FileCode2,
  Loader2,
  ShieldCheck,
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
import type { CalculatedField, TranspileResult } from "@/lib/transpile-types";
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

function statusVariant(status: CalculatedField["status"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "ok") return "default";
  if (status === "unparseable") return "secondary";
  if (status === "error") return "destructive";
  return "outline";
}

export function WorkbookConverter() {
  const inputRef = useRef<HTMLInputElement>(null);
  const { resolvedTheme } = useTheme();
  const [dragOver, setDragOver] = useState(false);
  const [state, setState] = useState<UiState>("empty");
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [result, setResult] = useState<TranspileResult | null>(null);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState(false);

  const filtered = useMemo(() => {
    if (!result) return [];
    const q = query.trim().toLowerCase();
    if (!q) return result.calculated_fields;
    return result.calculated_fields.filter((field) =>
      [field.caption, field.datasource, field.formula, field.sql, field.status]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [query, result]);

  const runFile = useCallback(async (file: File) => {
    setState("loading");
    setError(null);
    setFileName(file.name);
    setResult(null);
    const body = new FormData();
    body.append("file", file, file.name);
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
  }, []);

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

  const okCount = result?.calculated_fields.filter((f) => f.status === "ok").length ?? 0;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-10 md:py-16">
      <header className="flex flex-col gap-6">
        <div className="max-w-3xl space-y-4">
          <h1 className="font-heading text-4xl font-semibold tracking-tight md:text-5xl">
            Tableau formulas to ANSI SQL
          </h1>
          <p className="text-lg text-muted-foreground md:text-xl">
            Drop a <span className="font-medium text-foreground">.twb</span> or{" "}
            <span className="font-medium text-foreground">.twbx</span>. Calculated fields become warehouse-ready SQL —
            parsed in memory, never written to disk.
          </p>
        </div>
        <Alert className="max-w-3xl border-emerald-500/30 bg-emerald-500/5">
          <ShieldCheck className="text-emerald-600 dark:text-emerald-400" />
          <AlertTitle>Workbooks never leave this process</AlertTitle>
          <AlertDescription>
            The browser posts the file to a local Python CLI over stdin. Nothing is stored. For air-gapped conversion,
            skip this UI and use{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">python cli.py process</code> — full
            details are in the repository README.
          </AlertDescription>
        </Alert>
      </header>

      <Card>
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
              "flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center transition-colors",
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
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <button
              type="button"
              className={buttonVariants()}
              disabled={state === "loading"}
              onClick={() => void trySample()}
            >
              <Sparkles data-icon="inline-start" />
              Try with Sample Workbook
            </button>
            {fileName ? (
              <p className="text-sm text-muted-foreground">
                Last file: <span className="text-foreground">{fileName}</span>
              </p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {state === "empty" ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-muted-foreground">
            Drop a workbook to see SQL, an audit table, and Markdown.
          </CardContent>
        </Card>
      ) : null}

      {state === "loading" ? (
        <Card>
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
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge>{result.calculated_fields.length} fields</Badge>
            <Badge variant="secondary">{okCount} ok</Badge>
            <Badge variant="outline">{result.datasources.length} datasources</Badge>
            {result.workbook_name ? (
              <span className="text-muted-foreground">{result.workbook_name}</span>
            ) : null}
          </div>
          <Tabs defaultValue="sql">
            <TabsList>
              <TabsTrigger value="sql">
                <FileCode2 data-icon="inline-start" />
                SQL
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
              <div className="flex justify-end">
                <Button variant="outline" size="sm" onClick={() => void copySql()}>
                  {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
                  {copied ? "Copied" : "Copy"}
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
            <TabsContent value="audit" className="mt-3 space-y-3">
              <Input
                placeholder="Search caption, formula, SQL, status…"
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
        </div>
      ) : null}

      <Separator />
      <footer className="space-y-2 pb-8 text-sm text-muted-foreground">
        <p>
          CLI:{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
            python cli.py process workbook.twb --output-dir ./output --format all --print-summary
          </code>
        </p>
        <p>Requires Python 3.11+ and <code className="font-mono text-xs">pip install -r requirements.txt</code>.</p>
      </footer>
    </div>
  );
}
