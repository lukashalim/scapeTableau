import { FileSpreadsheet, Search, ShieldCheck, Terminal } from "lucide-react";
import Link from "next/link";

import { SiteFooter } from "@/components/site-footer";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { GITHUB_URL } from "@/lib/site";

interface FeatureRow {
  feature: string;
  ships: string;
}

const FEATURES: FeatureRow[] = [
  {
    feature: "In-memory workbook parse",
    ships:
      "Reads .twb / .twbx in process memory with stdlib xml.etree + zipfile. Hosted uploads are not written to disk and are not sent to an LLM.",
  },
  {
    feature: "Formula transpilation",
    ships:
      "IIF / ZN / IFNULL / CONTAINS / STARTSWITH (plus IF/CASE, LEN, TRIM, UPPER/LOWER, ABS, ROUND) become CASE / COALESCE / LIKE and ANSI SQL. LOD FIXED / INCLUDE / EXCLUDE stay isolated as -- TODO: Review LOD logic comments — they are not rewritten as SQL.",
  },
  {
    feature: "dbt-ready SQL views",
    ships:
      "Emits one CREATE VIEW per datasource (row-level calcs as columns; aggregates as comments) for dbt model files. schema.yml is not auto-written; this is not a full dbt package scaffold.",
  },
  {
    feature: "Extract Data",
    ships:
      "Packaged CSV / TSV / XLS / XLSX become a downloadable .xlsx. Custom SQL is copied to extra sheets, not executed. For Hyper, download the free BIEscape desktop converter.",
  },
  {
    feature: "Inspect Schema",
    ships:
      "Datasources, all columns (including hidden), parameters, redacted connections, and printable dashboard wireframes.",
  },
  {
    feature: "Air-gapped local run",
    ships:
      "CLI and Streamlit stay on-device. This hosted app is in-memory and non-persistent — it is not air-gapped.",
  },
];

const ARCHITECTURE = `                       [ .twb / .twbx bytes ]
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │    engine/parser.py  │ (xml.etree + zipfile)
                     └───────────┬──────────┘
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │    engine/schema.py  │ (Pydantic v2 Models)
                     └───────────┬──────────┘
                                 │
                                 ▼
          ┌──────────────────────┼──────────────────────┐
          ▼                      ▼                      ▼
 engine/transpiler.py     engine/extract.py      engine/inspect.py
          │                      │                      │
          └──────────┬───────────┴──────────┬───────────┘
                     ▼                      ▼
              TranspileResult         xlsx bytes
          (calculated_fields, sql_script,
           inspect, extract_inventory, dashboards)
                     │
            ┌────────┼────────┐
            ▼        ▼        ▼
       [ cli.py ] [ app_local.py ] [ /api/transpile + /api/extract ]`;

export function MarketingHero() {
  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 pt-12 md:pt-20">
      <div className="max-w-3xl space-y-5">
        <h1 className="font-heading text-4xl font-semibold tracking-tight text-balance md:text-5xl">
          Tableau to Power BI migration tool. Open a TWBX without Tableau. Convert Tableau workbooks to SQL.
        </h1>
        <p className="text-lg text-muted-foreground md:text-xl">
          BIEscape extracts Tableau calculated fields, packaged CSV/XLSX, and schema so you can migrate toward Power BI,
          warehouses, or dbt. It does not emit PBIX, LookML, or QuickSight assets. Hyper → Excel uses the free desktop
          converter.
        </p>
      </div>
    </section>
  );
}

export function MarketingPrivacyNote() {
  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col px-4 pb-10 md:pb-16">
      <Alert className="max-w-3xl border-emerald-500/30 bg-emerald-500/5">
        <ShieldCheck className="text-emerald-600 dark:text-emerald-400" />
        <AlertTitle>In-memory on this site. Air-gapped on your machine.</AlertTitle>
        <AlertDescription>
          Hosted conversion stays in process memory: no disk persist, no cloud object store, no LLM. That is not the
          same as air-gapped. For on-device conversion,{" "}
          <a href={GITHUB_URL} className="underline underline-offset-4" target="_blank" rel="noreferrer">
            clone the repo
          </a>{" "}
          and use <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">python cli.py process</code> or{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">streamlit run app_local.py</code>.
        </AlertDescription>
      </Alert>
    </section>
  );
}

export function MarketingBelow() {
  return (
    <div className="flex flex-col">
      <section className="border-t bg-muted/30">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-12 md:py-16">
          <h2 className="font-heading text-2xl font-semibold tracking-tight md:text-3xl">The Problem</h2>
          <div className="max-w-3xl space-y-4 text-muted-foreground">
            <p>
              Tableau workbooks are XML (or a zip of XML plus extracts). Calculated fields hide inside proprietary
              syntax — IIF, ZN, IFNULL, LODs — that warehouses do not run. Rebuilding those formulas by hand is slow,
              and packaged tables plus hidden columns stay buried unless you unzip the file yourself.
            </p>
            <p>
              Offloading the workbook to a generic LLM is a privacy non-starter for most corporate audits. You need a
              deterministic parser you can run locally, plus a hosted try-it path that does not keep the file.
            </p>
          </div>
        </div>
      </section>

      <section className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-12 md:py-16">
        <h2 className="font-heading text-2xl font-semibold tracking-tight md:text-3xl">How BIEscape Solves It</h2>
        <div className="hidden md:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/4">Capability</TableHead>
                <TableHead>What actually ships</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {FEATURES.map((row) => (
                <TableRow key={row.feature}>
                  <TableCell className="align-top font-medium whitespace-normal">{row.feature}</TableCell>
                  <TableCell className="whitespace-normal text-muted-foreground">{row.ships}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <div className="grid gap-3 md:hidden">
          {FEATURES.map((row) => (
            <Card key={row.feature} size="sm">
              <CardHeader>
                <CardTitle>{row.feature}</CardTitle>
                <CardDescription>{row.ships}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      </section>

      <section className="border-t bg-muted/30">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-12 md:py-16">
          <h2 className="font-heading text-2xl font-semibold tracking-tight md:text-3xl">Free Utilities</h2>
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FileSpreadsheet className="size-4" />
                  📊 Excel extract
                </CardTitle>
                <CardDescription>
                  Packaged tabular files become an .xlsx.{" "}
                  <Link href="/twbx-to-excel" className="underline underline-offset-4">
                    TWBX to Excel
                  </Link>
                  . For Hyper, download the desktop converter.
                </CardDescription>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Search className="size-4" />
                  🔍 Workbook inspector
                </CardTitle>
                <CardDescription>
                  Schema, parameters, hidden fields, and{" "}
                  <Link href="/twbx-to-pdf" className="underline underline-offset-4">
                    dashboard wireframes you can Print to PDF
                  </Link>{" "}
                  — passwords redacted.
                </CardDescription>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Terminal className="size-4" />
                  💻 Local CLI
                </CardTitle>
                <CardDescription>
                  <a href={GITHUB_URL} className="underline underline-offset-4" target="_blank" rel="noreferrer">
                    Clone the repo
                  </a>
                  . There is no PyPI package named scapetableau.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <pre className="overflow-x-auto rounded-lg bg-background p-3 font-mono text-xs leading-relaxed">
                  {`git clone ${GITHUB_URL}.git
pip install -r requirements.txt
python cli.py process sales_report.twbx --output-dir ./models`}
                </pre>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      <section className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-12 md:py-16">
        <h2 className="font-heading text-2xl font-semibold tracking-tight md:text-3xl">Architecture &amp; Security</h2>
        <p className="max-w-3xl text-muted-foreground">
          Python is the source of truth. The Next.js routes stream the upload to the engine and return JSON or xlsx.
          Nothing is stored after the response.
        </p>
        <pre className="overflow-x-auto rounded-xl border bg-muted/40 p-4 font-mono text-[11px] leading-snug md:text-xs">
          {ARCHITECTURE}
        </pre>
      </section>

      <SiteFooter />
    </div>
  );
}
