# BIEscape Converter (desktop)

Double-click app that converts Tableau `.hyper` / `.twbx` / `.tdsx` extracts to Excel via the same `extract_to_xlsx` path as the CLI.

## Run from source

```bash
pip install -r requirements.txt
pip install tableauhyperapi
python desktop/biescape_converter.py
```

## Unsigned downloads

GitHub Releases ship a **Windows installer** (`.exe`) and a macOS zip — both **unsigned** (v1):

- **Windows:** download `BIEscape-Converter-Setup-windows-x64.exe` → run. SmartScreen: **More info** → **Run anyway**.
- **macOS Gatekeeper:** right-click the app → **Open** → confirm.

Direct Windows link:  
`https://github.com/lukashalim/scapeTableau/releases/latest/download/BIEscape-Converter-Setup-windows-x64.exe`

Paid Apple notarization and Authenticode signing are not part of this slice.

## Build locally

```bash
pip install pyinstaller tableauhyperapi openpyxl
pyinstaller desktop/biescape_converter.spec
# dist/BIEscape-Converter/  (onedir)

# Windows installer (requires Inno Setup 6):
iscc desktop/installer.iss
# → dist-installer/BIEscape-Converter-Setup-windows-x64.exe
```

CI builds the Windows Setup `.exe` and macOS arm64 zip on tags `desktop-v*` and attaches them to the GitHub Release.
