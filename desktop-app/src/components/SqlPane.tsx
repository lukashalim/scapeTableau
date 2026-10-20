import type { SqlDialect } from "../../electron/types";

const DIALECTS: { value: SqlDialect; label: string }[] = [
  { value: "ansi", label: "ANSI" },
  { value: "snowflake", label: "Snowflake" },
  { value: "postgres", label: "Postgres" },
  { value: "duckdb", label: "DuckDB" },
];

type Props = {
  dialect: SqlDialect;
  onDialect: (d: SqlDialect) => void;
  sql: string;
  busy: boolean;
  hasFile: boolean;
  onRun: () => void;
  onCopy: () => void;
  onSave: () => void;
  copyFlash: boolean;
};

export function SqlPane({
  dialect,
  onDialect,
  sql,
  busy,
  hasFile,
  onRun,
  onCopy,
  onSave,
  copyFlash,
}: Props) {
  return (
    <div className="sql-pane">
      <div className="sql-toolbar">
        <label className="dialect">
          <span>Dialect</span>
          <select
            value={dialect}
            disabled={busy}
            onChange={(e) => onDialect(e.target.value as SqlDialect)}
          >
            {DIALECTS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <div className="sql-actions">
          <button
            type="button"
            className="browse"
            disabled={busy || !hasFile}
            onClick={onRun}
          >
            Generate SQL
          </button>
          <button
            type="button"
            className="browse"
            disabled={!sql || busy}
            onClick={onCopy}
          >
            {copyFlash ? "Copied" : "Copy"}
          </button>
          <button
            type="button"
            className="browse"
            disabled={!sql || busy}
            onClick={onSave}
          >
            Save .sql…
          </button>
        </div>
      </div>
      <textarea
        className="sql-editor"
        readOnly
        spellCheck={false}
        value={sql}
        placeholder={
          hasFile
            ? "Click Generate SQL to transpile calculated fields locally."
            : "Select a .twb / .twbx workbook first."
        }
      />
    </div>
  );
}
