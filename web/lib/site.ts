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
      "Extract CSV, TSV, XLS, and XLSX files packaged inside a Tableau .twbx into a downloadable Excel workbook. Hyper extracts are listed but not converted.",
    h1: "TWBX to Excel: packaged tables, not Hyper",
    lede: "BIEscape unpacks tabular files inside a .twbx into one .xlsx. Custom SQL is copied onto extra sheets and is not executed. Embedded .hyper extracts are listed only.",
    capabilities: [
      "Convert packaged CSV / TSV / XLS / XLSX members into a downloadable .xlsx.",
      "Copy Custom SQL text onto extra sheets (not run against a warehouse).",
      "Inventory archive members, including .hyper paths.",
    ],
    limitations: [
      ".hyper extracts are listed but not converted to Excel on this site or on Vercel.",
      "A plain .twb has no packaged tables. Use a .twbx that includes CSV/XLSX extracts.",
    ],
    faqs: [
      {
        question: "Can BIEscape turn a TWBX into Excel?",
        answer:
          "It can turn packaged CSV, TSV, XLS, and XLSX files inside the .twbx into a downloadable .xlsx. That is not a Hyper-to-Excel converter.",
      },
      {
        question: "Are .hyper extracts converted?",
        answer:
          "No. Hyper files are listed so you can see they exist. Excel conversion of Hyper is not available on the hosted engine.",
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
      "Transpile Tableau calculated fields (IIF, ZN, IFNULL, CONTAINS, and more) into ANSI SQL and dbt-ready CREATE VIEW scripts. LOD expressions become TODO comments.",
    h1: "Convert a Tableau workbook to SQL",
    lede: "BIEscape transpiles calculated fields into ANSI SQL you can drop into Snowflake, BigQuery, Postgres, DuckDB, or a dbt model file. It does not rebuild the full semantic layer or write schema.yml.",
    capabilities: [
      "Map IIF, IF/THEN, ZN, IFNULL, CONTAINS, STARTSWITH, LEN, TRIM, UPPER/LOWER, ABS, ROUND to ANSI SQL.",
      "Emit a CREATE VIEW sql_script plus a markdown audit of each field.",
      "Leave FIXED / INCLUDE / EXCLUDE LODs as -- TODO: Review LOD logic comments.",
    ],
    limitations: [
      "Unknown functions become SQL comments rather than guessed translations.",
      "dbt output is drop-in SQL, not a generated schema.yml or package scaffold.",
    ],
    faqs: [
      {
        question: "Does BIEscape convert Tableau to SQL?",
        answer:
          "It transpiles calculated-field formulas to ANSI SQL and a CREATE VIEW script. Datasource joins, LOD logic, and table calcs are not fully rewritten.",
      },
      {
        question: "Are LOD calculations converted?",
        answer:
          "FIXED, INCLUDE, and EXCLUDE stay isolated as TODO comments. They are not rewritten as SQL.",
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
      "BIEscape extracts Tableau calculated fields, packaged tables, and schema so you can migrate toward Power BI. It does not emit PBIX or Power Query today.",
    h1: "Tableau to Power BI: extract the logic first",
    lede: "A Power BI migration still needs DAX, Power Query, and a PBIX. BIEscape gives you the Tableau side of that work: SQL from calculated fields, Excel from packaged CSV/XLSX, and a schema inspect. It does not generate a Power BI file.",
    capabilities: [
      "SQL transpile of Tableau calculated fields for warehouse or Power Query reimplementation.",
      "Excel extract of CSV/XLSX packaged in the twbx (not Hyper).",
      "Schema inspect: columns, hidden fields, parameters, dashboard wireframes.",
    ],
    limitations: [
      "BIEscape does not emit PBIX, PBIT, DAX, or Power Query M.",
      "Hyper extracts are listed but not converted.",
    ],
    faqs: [
      {
        question: "Does BIEscape convert Tableau to Power BI?",
        answer:
          "No. It extracts Tableau calculations, packaged tabular data, and schema so you can rebuild in Power BI. It does not write a PBIX.",
      },
      {
        question: "What should I use it for in a PBI migration?",
        answer:
          "Inventory fields, copy transpiled SQL as a starting point for measures or warehouse views, and export packaged CSV/XLSX when Hyper is not involved.",
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
      "LOD calculations remain TODO comments, not LookML native derived tables.",
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
];

export const LANDING_SLUGS = LANDING_PAGES.map((page) => page.slug);

export function getLandingPage(slug: string): LandingPage | undefined {
  return LANDING_PAGES.find((page) => page.slug === slug);
}

export const DEFAULT_TITLE =
  "Tableau to Power BI migration tool — open TWBX without Tableau, convert to SQL | BIEscape";

export const DEFAULT_DESCRIPTION =
  "BIEscape is a Tableau to Power BI migration helper: open a TWBX without Tableau, convert Tableau calculated fields to SQL, extract packaged Excel/CSV, and inspect schema. Hyper extracts are listed but not converted. Not a PBIX, LookML, or QuickSight exporter.";
