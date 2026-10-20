# BIEscape Converter (desktop)

Electron + React UI that converts Tableau `.hyper` / `.twbx` / `.tdsx` extracts to Excel via the same `extract_to_xlsx` engine as the CLI.

Conversion runs **locally** through a headless Python sidecar (`desktop/convert_cli.py`). Nothing is uploaded.

## App location

The shipped UI lives in [`desktop-app/`](../desktop-app/).

```bash
# Dev (Python sidecar via python3 + Vite + Electron)
cd desktop-app
npm install
npm run dev
```

## Headless sidecar

```bash
python desktop/convert_cli.py convert samples/tiny_extract.hyper /tmp/out.xlsx
python desktop/convert_cli.py transpile samples/superstore_calcs.twb --dialect ansi
# → one JSON line on stdout
```

The Electron app drop zone selects a file; use **Extract** to save Excel or **SQL** to generate / copy / save a local SQL script (dialects: ansi, snowflake, postgres, duckdb).

PyInstaller onedir (used by the Windows installer):

```bash
pip install tableauhyperapi pyinstaller
pyinstaller desktop/convert_cli.spec --noconfirm --clean
# → dist/biescape-convert/
```

## Windows installer

CI on tags `desktop-v*` builds the sidecar, then `electron-builder` NSIS:

`BIEscape-Converter-Setup-windows-x64.exe`

Direct download:

`https://github.com/lukashalim/scapeTableau/releases/latest/download/BIEscape-Converter-Setup-windows-x64.exe`

Local pack (after sidecar build):

```bash
cd desktop-app
npm run pack:win
# → desktop-app/release/BIEscape-Converter-Setup-windows-x64.exe
```

## Legacy Tkinter

`biescape_converter.py` remains for local experiments but is **not** shipped in new releases.
