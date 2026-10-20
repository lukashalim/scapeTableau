# BIEscape Converter (Electron)

Windows desktop UI for local Hyper / TWBX / TDSX → Excel.

## Develop

Requires Python 3.11+ with `requirements.txt` + `tableauhyperapi` on `PATH` as `python3` (or set `BIESCAPE_PYTHON`).

```bash
npm install
npm run dev
```

### SQL tab

1. Drop or browse a `.twb` / `.twbx` (also `.hyper` / `.tdsx`).
2. Open the **SQL** tab → pick dialect → **Generate SQL**.
3. **Copy** or **Save .sql…**. Conversion stays local via `desktop/convert_cli.py transpile`.

## Build installer (Windows)

```bash
# from repo root — build Python sidecar first
pyinstaller desktop/convert_cli.spec --noconfirm --clean

cd desktop-app
npm run pack:win
# → release/BIEscape-Converter-Setup-windows-x64.exe
```

CI does this on tags `desktop-v*`.
