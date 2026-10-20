# BIEscape Converter (desktop)

Double-click app that converts Tableau `.hyper` / `.twbx` / `.tdsx` extracts to Excel via the same `extract_to_xlsx` path as the CLI.

## Run from source

```bash
pip install -r requirements.txt
pip install tableauhyperapi
python desktop/biescape_converter.py
```

## Unsigned downloads

GitHub Release zips are **unsigned** (v1):

- **macOS Gatekeeper:** right-click the app → **Open** → confirm.
- **Windows SmartScreen:** **More info** → **Run anyway**.

Paid Apple notarization and Authenticode signing are not part of this slice.

## Build locally

```bash
pip install pyinstaller tableauhyperapi openpyxl
pyinstaller desktop/biescape_converter.spec
# dist/BIEscape-Converter/  (onedir)
```

CI builds Windows x64 and macOS arm64 on tags `desktop-v*` and attaches zip archives to the GitHub Release.
