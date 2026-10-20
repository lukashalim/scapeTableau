export const SITE_URL = "https://www.biescape.com";
export const SITE_NAME = "BIEscape";
export const GITHUB_URL = "https://github.com/lukashalim/scapeTableau";

export interface FaqItem {
  question: string;
  answer: string;
}

export interface LandingPage {
  slug: string;
  title: string;
  description: string;
  h1: string;
  lede: string;
  capabilities: string[];
  limitations: string[];
  faqs: FaqItem[];
}

export const LANDING_PAGES: LandingPage[] = [
  {
    slug: "twbx-viewer",
    title: "Open a TWBX file without Tableau | BIEscape",
    description:
      "Inspect a Tableau .twb or .twbx in the browser: datasources, hidden columns, parameters, and dashboard wireframes. BIEscape does not run Tableau Desktop.",
    h1: "Open a TWBX file without Tableau",
    lede: "Drop a workbook on BIEscape to read packaged XML in memory. You get schema, connections with passwords redacted, and printable dashboard wireframes — not a full Tableau viz renderer.",
    capabilities: [
      "Parse .twb and .twbx in process memory (no disk persist, no LLM).",
      "List datasources, all columns including hidden fields, and parameters.",
      "Show dashboard zone wireframes you can print.",
    ],
    limitations: [
      "This is not Tableau Desktop or Tableau Server. Charts, filters, and interactivity are not rendered.",
      "Live database queries are not executed. Connection details are inspected, not used.",
    ],
    faqs: [
      {
        question: "Can I open a TWBX without installing Tableau?",
        answer:
          "Yes. BIEscape parses the workbook XML (and the zip for .twbx) in the browser upload path so you can inspect schema and layout without Tableau Desktop.",
      },
      {
        question: "Does BIEscape render the actual Tableau dashboard?",
        answer:
          "No. Dashboards appear as HTML/SVG wireframes of layout zones. Use Print to PDF for a layout snapshot, not a pixel-perfect Tableau export.",
      },
      {
        question: "Are my workbooks stored?",
        answer:
          "Hosted conversion stays in process memory for the request. Files are not persisted. For air-gapped use, clone the GitHub repo and run the CLI on your machine.",
      },
    ],
  },
  {
    slug: "twbx-to-excel",
    title: "Convert TWBX packaged tables to Excel | BIEscape",
    description:
      "Extract CSV, TSV, XLS, and XLSX files packaged inside a Tableau .twbx into a downloadable Excel workbook. For Hyper extracts, download the free BIEscape desktop converter.",
    h1: "TWBX to Excel: packaged tables + desktop Hyper",
    lede: "BIEscape unpacks CSV/TSV/XLS/XLSX inside a .twbx into one .xlsx in the browser. Hyper needs Tableau’s hyperd engine — download the free BIEscape Converter for Windows and macOS.",
    capabilities: [
      "Convert packaged CSV / TSV / XLS / XLSX members into a downloadable .xlsx in the browser.",
      "Copy Custom SQL text onto extra sheets (not run against a warehouse).",
      "Download BIEscape Converter from GitHub Releases for .hyper / .twbx / .tdsx → Excel on your computer.",
    ],
    limitations: [
      ".hyper cannot be converted in the browser or on Vercel (hyperd is too large for serverless).",
      "Desktop builds are unsigned v1 — macOS Gatekeeper and Windows SmartScreen may warn (Open anyway).",
      "A plain .twb has no packaged tables. Use a .twbx that includes CSV/XLSX extracts, or a .hyper/.tdsx in the desktop app.",
    ],
    faqs: [
      {
        question: "Can BIEscape turn a TWBX into Excel?",
        answer:
          "Yes for packaged CSV, TSV, XLS, and XLSX inside the .twbx. For .hyper extracts, download the free desktop converter from GitHub Releases.",
      },
      {
        question: "Are .hyper extracts converted?",
        answer:
          "Not in the browser. Download BIEscape Converter from https://github.com/lukashalim/scapeTableau/releases/latest (Windows x64 and macOS arm64 zips). Unsigned v1 may trigger Gatekeeper or SmartScreen — Open anyway.",
      },
      {
        question: "Does Custom SQL run?",
        answer:
          "No. Custom SQL is copied to extra sheets for review. BIEscape does not execute it.",
      },
    ],
  },
  {
    slug: "twbx-to-pdf",
    title: "Print Tableau dashboard wireframes to PDF | BIEscape",
    description:
      "Print Tableau dashboard layouts from a TWB or TWBX as wireframe PDF via the browser print dialog. Not a pixel-perfect Tableau export.",
    h1: "TWBX to PDF: dashboard wireframes",
    lede: "BIEscape builds an HTML/SVG wireframe of each dashboard’s layout zones. Use Print in the Inspect Schema tab and choose Save as PDF in the browser dialog.",
    capabilities: [
      "Read dashboard names, pixel size, and zone layout from the workbook.",
      "Open a printable HTML/SVG wireframe (browser Print → PDF).",
    ],
    limitations: [
      "This is not Tableau’s Print to PDF. Marks, colors, and data are not rendered.",
      "Pop-up blockers can stop the print window; allow pop-ups for this site.",
    ],
    faqs: [
      {
        question: "Can I export a Tableau dashboard to PDF without Tableau?",
        answer:
          "You can print BIEscape’s layout wireframe to PDF. You do not get a Tableau-rendered dashboard PDF.",
      },
      {
        question: "Where is the print control?",
        answer:
          "Upload a workbook on the converter, open Inspect Schema, and use Print on each dashboard card.",
      },
      {
        question: "Does this include live data?",
        answer:
          "No. Wireframes are layout only. Data extracts are handled separately (tabular files to Excel; Hyper listed only).",
      },
    ],
  },
  {
    slug: "tableau-to-sql",
    title: "Convert Tableau calculated fields to SQL | BIEscape",
    description:
      "Transpile Tableau calculated fields (IIF, ZN, IFNULL, CONTAINS, FIXED/INCLUDE/EXCLUDE LOD) into ANSI SQL and dbt-ready CREATE VIEW scripts. DAX is generated in the same pass.",
    h1: "Convert a Tableau workbook to SQL",
    lede: "BIEscape transpiles calculated fields into ANSI SQL you can drop into Snowflake, BigQuery, Postgres, DuckDB, or a dbt model file. It does not rebuild the full semantic layer or write schema.yml.",
    capabilities: [
      "Map IIF, IF/THEN, ZN, IFNULL, CONTAINS, STARTSWITH, LEN, TRIM, UPPER/LOWER, ABS, ROUND, CASE, and date functions to ANSI SQL.",
      "Emit FIXED LOD as window functions or grain CTEs/subqueries; INCLUDE/EXCLUDE at the worksheet view grain.",
      "Emit a CREATE VIEW sql_script (dbt config only with --format dbt) plus a markdown audit of each field.",
    ],
    limitations: [
      "Unknown functions become SQL comments rather than guessed translations.",
      "dbt output is drop-in SQL, not a generated schema.yml or package scaffold. Table calculations stay as TODOs.",
    ],
    faqs: [
      {
        question: "Does BIEscape convert Tableau to SQL?",
        answer:
          "It transpiles calculated-field formulas to ANSI SQL and a CREATE VIEW script. Datasource joins and table calcs are not fully rewritten.",
      },
      {
        question: "Are LOD calculations converted?",
        answer:
          "FIXED, INCLUDE, and EXCLUDE are rewritten as SQL window functions or grain subqueries. INCLUDE/EXCLUDE need a worksheet view grain (for example Sheet 1 rows/cols).",
      },
      {
        question: "Can I run this locally?",
        answer:
          "Yes. Clone https://github.com/lukashalim/scapeTableau, then pip install -r requirements.txt and python cli.py process.",
      },
    ],
  },
  {
    slug: "tableau-to-power-bi",
    title: "Tableau to Power BI migration helper | BIEscape",
    description:
      "BIEscape extracts Tableau calculated fields to SQL and DAX, plus packaged tables and schema, so you can migrate toward Power BI. It does not emit PBIX.",
    h1: "Tableau to Power BI: extract the logic first",
    lede: "A Power BI migration still needs a PBIX and Power Query. BIEscape gives you the Tableau side of that work: SQL and DAX from calculated fields, Excel from packaged CSV/XLSX, and a schema inspect. It does not generate a Power BI file.",
    capabilities: [
      "SQL and DAX transpile of Tableau calculated fields for warehouse views or Power BI measures.",
      "Excel extract of CSV/XLSX packaged in the twbx (not Hyper).",
      "Schema inspect: columns, hidden fields, parameters, dashboard wireframes.",
    ],
    limitations: [
      "BIEscape does not emit PBIX, PBIT, or Power Query M. LOD expressions stay as DAX TODO comments.",
      "Hyper extracts are listed but not converted.",
    ],
    faqs: [
      {
        question: "Does BIEscape convert Tableau to Power BI?",
        answer:
          "It transpiles calculated fields to DAX expressions and SQL, and extracts packaged tables. It does not write a PBIX.",
      },
      {
        question: "What should I use it for in a PBI migration?",
        answer:
          "Inventory fields, copy transpiled DAX or SQL as a starting point for measures, and export packaged CSV/XLSX when Hyper is not involved.",
      },
      {
        question: "Where do I start?",
        answer:
          "Open the converter on the homepage, drop a .twb or .twbx, then review Transpile, Extract Data, and Inspect Schema.",
      },
    ],
  },
  {
    slug: "tableau-to-looker",
    title: "Tableau to Looker migration helper | BIEscape",
    description:
      "BIEscape extracts Tableau calculated fields and schema for Looker migrations. It does not emit LookML, explores, or Looker dashboards.",
    h1: "Tableau to Looker: extract logic, not LookML",
    lede: "Looker needs LookML views and explores. BIEscape does not write those. It transpiles Tableau formulas to SQL and inspects workbook schema so you can port logic into Looker by hand.",
    capabilities: [
      "ANSI SQL from Tableau calculated fields as a starting point for LookML sql: fields or warehouse views.",
      "Schema inspect of datasources, hidden columns, and parameters.",
      "Excel extract of packaged CSV/XLSX (not Hyper).",
    ],
    limitations: [
      "BIEscape does not emit LookML, looks, or Looker dashboards.",
      "INCLUDE/EXCLUDE LOD SQL needs a worksheet grain; table calcs stay as TODOs. LookML is not generated.",
    ],
    faqs: [
      {
        question: "Does BIEscape convert Tableau to Looker?",
        answer:
          "No. It extracts Tableau SQL-shaped logic and schema. You still author LookML in Looker.",
      },
      {
        question: "Can I get a .lkml file?",
        answer:
          "Not from BIEscape today. Use the SQL script and field list as input for a manual LookML port.",
      },
      {
        question: "Does this replace a semantic-layer migration tool?",
        answer:
          "No. It is a workbook parser and formula transpiler, not a Looker importer.",
      },
    ],
  },
  {
    slug: "tableau-to-quicksight",
    title: "Tableau to QuickSight migration helper | BIEscape",
    description:
      "BIEscape extracts Tableau calculated fields, packaged tables, and schema for Amazon QuickSight migrations. It does not emit QuickSight analyses or templates.",
    h1: "Tableau to QuickSight: extract, then rebuild",
    lede: "Amazon QuickSight analyses are not generated here. BIEscape pulls Tableau calculations into SQL, packaged CSV/XLSX into Excel, and schema into an inspect view so you can rebuild datasets in QuickSight.",
    capabilities: [
      "SQL transpile of calculated fields for Athena/Redshift/warehouse datasets.",
      "Excel extract of packaged CSV/XLSX in the twbx (not Hyper).",
      "Schema and dashboard wireframe inspect for layout reference.",
    ],
    limitations: [
      "BIEscape does not emit QuickSight JSON, analyses, dashboards, or templates.",
      "Hyper extracts are listed but not converted.",
    ],
    faqs: [
      {
        question: "Does BIEscape convert Tableau to QuickSight?",
        answer:
          "No. It extracts Tableau logic and packaged tabular data so you can rebuild in QuickSight. It does not create QuickSight assets.",
      },
      {
        question: "What output can I take into AWS?",
        answer:
          "A SQL script for calculated fields, an .xlsx of packaged CSV/XLSX, and a field inventory. Upload data to your AWS source, then recreate visuals in QuickSight.",
      },
      {
        question: "Are Hyper extracts included?",
        answer:
          "They are listed in the extract inventory and are not converted on the hosted engine.",
      },
    ],
  },
  {
    slug: "tableau-to-dax",
    title: "Convert Tableau calculated fields to DAX | BIEscape",
    description:
      "Transpile Tableau calculated fields (IIF, ZN, IFNULL, CONTAINS, IF/THEN) into DAX expressions for Power BI measures. LOD expressions stay as TODO comments. No PBIX export.",
    h1: "Convert Tableau calculations to DAX",
    lede: "BIEscape maps Tableau calculated-field formulas to DAX in the same parse as SQL. Use the Transpile workspace DAX view to copy measures. It does not emit a PBIX or load Hyper extracts.",
    capabilities: [
      "IIF → IF, ZN → IF(ISBLANK(), 0, x), IFNULL → IF(ISBLANK()), CONTAINS → CONTAINSSTRING.",
      "IF / THEN / ELSEIF as nested IF; field names stay [Field Name].",
      "Same upload fills SQL and DAX so you can compare warehouse views with Power BI measures.",
    ],
    limitations: [
      "FIXED / INCLUDE / EXCLUDE LOD expressions are DAX TODO comments, not CALCULATE/ALLEXCEPT rewrites.",
      "No PBIX, PBIT, or Power Query M export. Hyper extracts are listed but not converted.",
    ],
    faqs: [
      {
        question: "Does BIEscape convert Tableau to DAX?",
        answer:
          "Yes for calculated-field expressions (IIF, ZN, IFNULL, CONTAINS, IF/THEN, aggregations). It does not build a Power BI data model file.",
      },
      {
        question: "Are LOD calculations converted to DAX?",
        answer:
          "Not yet. LOD formulas are emitted as // TODO: Review LOD logic comments. SQL transpile does rewrite LODs as windows or grain subqueries.",
      },
      {
        question: "Where do I copy DAX?",
        answer:
          "Upload a workbook on the homepage converter, open Transpile, then the DAX tab. CLI: python cli.py process file.twb --target dax --format dax.",
      },
    ],
  },
];

export const LANDING_SLUGS = LANDING_PAGES.map((page) => page.slug);

export function getLandingPage(slug: string): LandingPage | undefined {
  return LANDING_PAGES.find((page) => page.slug === slug);
}

export const DEFAULT_TITLE =
  "Tableau to Power BI migration tool — open TWBX without Tableau, convert to SQL | BIEscape";

export const DEFAULT_DESCRIPTION =
  "BIEscape is a Tableau to Power BI migration helper: open a TWBX without Tableau, convert Tableau calculated fields to SQL, extract packaged Excel/CSV, and inspect schema. Hyper extracts are listed but not converted. Not a PBIX, LookML, or QuickSight exporter.";
